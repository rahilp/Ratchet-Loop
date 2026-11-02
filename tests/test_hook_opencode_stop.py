#!/usr/bin/env python3
"""
OpenCode support, step 6: on OpenCode, when the agent ends its turn with a gate still open, Ratchet's plugin sends it
back to work with the engine's reason (a best-effort stop: the turn already ended and the user saw it end), only a few
times in a row without progress, and never for a sub-agent, a session the user cancelled, or a session with nothing to
block.

    python3 tests/test_hook_opencode_stop.py      # needs node on PATH; builds throwaway git projects

Why this file is adversarial. OpenCode has no hook that can hold a turn open. The plugin's `event` hook is called
fire-and-forget (`void hook["event"]?.(...)`), so the only emulation is: on `session.idle`, ask `hook stop --host
opencode` and, on a block, send a NEW user-role message with `client.session.prompt`. Every re-prompt costs the
developer paid model quota and takes over their session, so a wrong loop guard (no cap, a cap shared between sessions,
a re-prompt that counts as the user typing, a re-prompt of a sub-agent or of a session the user just cancelled, two
re-prompts at once, an exception thrown into OpenCode) is a real harm. Every cap and every skip is observed as the
fake client's prompt-call count, never by reading a constant in the plugin, and every verdict is shown to be able to
fail with mutant plugins (a reference stop plugin written in this file, run through the same driver and predicates,
next to the same plugin with exactly one thing broken).

The loop guard end to end ([CP6-T02]). The plugin's own caps are the first line; the ENGINE's `max_stop_blocks` valve is
the second, and it only holds because the plugin's re-prompt is never recorded as a typed reply (CP5), so the
engine's stop count keeps growing across the echoed messages. The test sets the valve BELOW the plugin's cap, feeds each
re-prompt back through `chat.message` as OpenCode really does, and requires the engine to halt the loop (the run is
marked blocked for the user); a mutant without plugin caps must still be stopped by the engine, and a mutant that
records the echo as typed must be caught.

How a case runs. A throwaway git project (temp dir, never this repo's .ratchet/) gets a run in its building phase (gate
open) and the plugin is installed there by the public command (`ratchet.py install-hooks --host opencode`). A node
driver, written to a separate temp folder, imports the installed (rendered) plugin file, calls its exported factory
with a fake `{client, directory, worktree}` and replays OpenCode-shaped events (`hooks.event({event: {id, type,
properties}})`) and `chat.message` calls. The fake client records every call, counts `session.prompt` (or the SDK's
`promptAsync`) calls, answers `session.get` from a table (parent / no parent / rejects / throws), and its prompt can
resolve, stay pending until released, reject, throw, and echo the re-prompt back to the plugin's `chat.message` hook as
a user-role message. The REAL engine answers inside the project (a stand-in engine only where a case needs a reason
that changes on every call, or a damaged answer). A spy `python3` first on the node process's PATH logs every engine
call (argv, stdin, stdout), so a test can see what the plugin asked and what the engine said.

Contract the driver relies on (taken from the plan: "each step awaits the handler"): the promise the plugin's `event`
hook returns settles when the plugin has finished handling that event, including the `session.prompt` call (OpenCode
itself ignores that promise, so awaiting it is only how this file synchronises; there are no sleeps). A prompt left
pending on purpose ([CP6-T09], [CP6-T15]) is observed through the fake client's call count instead.
The plugin's own marked text comes from its `ratchetMarkedText(reason)` seam (CP5), found the way CP5's tests find it.
Timers: the driver wraps `setTimeout` so a test can list the timers a handler left behind and fire them by hand
([CP6-T10]: no timer-driven retry).

Abort signal. Read from OpenCode source v1.19.5 (packages/opencode/src/session/processor.ts `halt`, prompt.ts): a
turn cancelled in mid-stream publishes `session.error` with `properties = {sessionID, error: {name:
'MessageAbortedError', ...}}` and then `session.idle`, so that cancel signal arrives BEFORE the idle. [CP6-T12] replays
exactly that one shape. Other cancels (during a provider retry wait, between loop steps, of the user's own `!` command)
publish no session.error; the plugin reads those from an assistant message.updated carrying the error, from an idle that
follows a status `retry`, and from the session's last assistant message, all pinned in
tests/test_hook_opencode_stop_extra.py ([CP6-X01] ... [CP6-X07]).

Every child process gets an ambient-proof environment (every CODEX_*, CLAUDE*, GEMINI_*, COPILOT_*, GITHUB_COPILOT_*,
OPENCODE_*, XDG_* and RATCHET_* variable removed; a throwaway HOME, RATCHET_HOME and XDG_CONFIG_HOME), so nothing here
touches the real ~/.config/opencode or this repo's .ratchet/. This file never loads ratchet.py as a module and never
reads its source text (it runs processes; HOST_ENFORCEMENT is read by running the engine file in a child process;
README.md is read as plain text), so it needs no test-inventory entry. A missing `node` FAILS the script loudly at its
start ([CP6-T20] runs this very script with a PATH that has no node).

Where this file narrows or reads the plan text:
  * the real engine cannot be made to change its reason on every call, so [CP6-T06] uses a stand-in engine that
    answers `hook stop` with a new reason each call (the plan allows it), and so do the ceiling halves of [CP6-T07];
  * [CP6-T17] reads "the engine's own count of five failed stops is never exceeded" as: after six idles the engine
    never answered "letting you stop after repeated failures" (its count never passed 5), which also means the plugin
    stops ASKING the engine once it has stopped re-prompting;
  * a skipped session (a sub-agent, a failed lookup, a cancelled session) must be skipped BEFORE the engine is asked:
    the engine's stop count is shared by the whole run, so asking it for a session that is not re-prompted would burn
    the run's safety valve ([CP6-T11], [CP6-T12]);
  * the README test-list line ([CP6-T19]) and the HOST_ENFORCEMENT / README stop cell ([CP6-T18]) are checked, not
    edited, here.

Each case id appears in its check names as [CP6-Txx]. When RATCHET_TEST_REPORT is set, one JSON entry per planned case
({"cases": [{"id", "status", "message"}]}) is written there.
"""
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text
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
README = README_DOCS
THIS_FILE = Path(__file__).resolve()
NEW_TEST_LINE = "python3 tests/test_hook_opencode_stop.py"
PLANNED = [f"CP6-T{n:02d}" for n in range(1, 21)]
PASS, FAIL = 0, 0
RESULTS = {}   # case id -> [(ok, name, detail)]
CRASHED = {}   # case id -> what crashed
NODE = shutil.which("node")
PREFIX = "[RATCHET PLUGIN TURN - NOT USER INPUT]"
_STRIP_PREFIXES = ("CODEX_", "OPENCODE_", "XDG_", "RATCHET_", "GEMINI_", "COPILOT_", "GITHUB_COPILOT_", "CLAUDE")
STOP_CELL = "No, the stop hook cannot stop the agent from ending its turn early."
FAKE_TOKEN = "00000000-0000-4000-8000-000000000000"


def check(name, cond, detail=""):
    global PASS, FAIL
    m = re.match(r"\[(CP6-[A-Z]\d+)\]", name)
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
        self.home = Path(_tmp.mkdtemp(prefix="ratchet-ocstop-h-")).resolve()
        self.rh = Path(_tmp.mkdtemp(prefix="ratchet-ocstop-r-")).resolve()
        self.work = Path(_tmp.mkdtemp(prefix="ratchet-ocstop-w-")).resolve()
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
        self.run_dir = self.proj / ".ratchet" / "runs" / "hk"

    def env(self, **extra):
        env = scrubbed_env()
        env.update(HOME=str(self.s.home), RATCHET_HOME=str(self.s.rh), XDG_CONFIG_HOME=str(self.s.home / ".config"),
                   PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return env

    def sh(self, *args, stdin=None, env_extra=None, timeout=180):
        return subprocess.run([sys.executable, str(ENGINE), *args], cwd=str(self.proj), input=stdin,
                              capture_output=True, text=True, env=self.env(**(env_extra or {})), timeout=timeout)


def _must(r):
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    return r


def _checkpoints(run):
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "exit 1"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))


TEMPLATES = {}


def template(kind):
    """kind 'building': an approved run in its building phase with an open gate (the next required action is to write
    the tests; `tests-written CP1` moves it on once t.test.js holds the planned case). kind 'bare': a git project with
    no .ratchet folder. Built once, copied per case."""
    if kind in TEMPLATES:
        return TEMPLATES[kind]
    s = scratch()
    base = Path(_tmp.mkdtemp(prefix=f"ratchet-ocstop-t-{kind}-")).resolve()
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(base, *c)
    (base / "a.js").write_text("x")
    git(base, "add", "-A")
    git(base, "commit", "-qm", "init")
    w = World(base)
    if kind == "building":
        import datetime as dt
        _must(w.sh("init", "opencode stop", "--slug", "hk"))
        _checkpoints(base / ".ratchet" / "runs" / "hk")
        _must(w.sh("plan-submit"))
        _must(w.sh("install-hooks", "--host", "opencode"))
        _must(w.sh("ask", "approve-plan"))
        tp = s.work / f"{base.name}-transcript.jsonl"   # what the agent CLI writes when the user types
        tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "approved"},
                                  "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
        _must(w.sh("hook", "prompt", "--host", "claude",
                   stdin=json.dumps({"cwd": str(base), "prompt": "approved", "transcript_path": str(tp)})))
        _must(w.sh("approve-plan"))
    TEMPLATES[kind] = base
    return base


def fresh(kind="building", max_stop_blocks=None):
    """A new clean copy of a template, as a World. `max_stop_blocks` sets the engine's safety valve in the copy."""
    src = template(kind)
    dst = Path(_tmp.mkdtemp(prefix=f"ratchet-ocstop-p-{kind}-")).resolve()
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    w = World(dst)
    if max_stop_blocks is not None:
        _must(w.sh("config", "--set", f"max_stop_blocks={max_stop_blocks}"))
    return w


def real_plugin():
    """The rendered plugin installed in the building template (read only: copies are retargeted, never edited)."""
    return template("building") / ".opencode" / "plugins" / "ratchet.js"


# ------------------------------------------------------------------------------- spy python3 and the node driver

SPY_PY = r'''#!__REAL__
import json, os, subprocess, sys
data = sys.stdin.buffer.read()
p = subprocess.run(["__REAL__"] + sys.argv[1:], input=data, capture_output=True)
rec = {"argv": sys.argv[1:], "stdin": data.decode("utf-8", "replace"), "stdout": p.stdout.decode("utf-8", "replace"),
       "rc": p.returncode}
with open(os.environ["OCSPY_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
sys.stdout.buffer.write(p.stdout)
sys.stderr.buffer.write(p.stderr)
sys.exit(p.returncode)
'''

DRIVER_JS = r"""
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';

// Timers: every setTimeout the plugin makes is listed, so a test can see which are left and fire them by hand.
const realST = globalThis.setTimeout, realCT = globalThis.clearTimeout;
const timers = [];
globalThis.setTimeout = function (fn, ms, ...args) {
  const rec = { ms: Number(ms) || 0, done: false, fn, args };
  rec.h = realST(() => { if (!rec.done) { rec.done = true; if (typeof fn === 'function') fn(...args); } }, ms);
  timers.push(rec);
  return rec.h;
};
globalThis.clearTimeout = function (h) { for (const r of timers) if (r.h === h) r.done = true; return realCT(h); };

const scen = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const problems = [];
process.on('unhandledRejection', (e) => { problems.push('unhandledRejection: ' + ((e && e.message) || String(e))); });
process.on('uncaughtException', (e) => { problems.push('uncaughtException: ' + ((e && e.message) || String(e))); });
const vars = {};
let loads = 0, evn = 0;
const KNOWN = new Set(['event', 'config', 'tool', 'auth', 'provider', 'chat.message', 'chat.params', 'chat.headers',
  'permission.ask', 'command.execute.before', 'tool.execute.before', 'tool.execute.after', 'tool.definition',
  'shell.env', 'dispose']);

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
function msgOf(e) { return (e !== null && (typeof e === 'object' || typeof e === 'string') && e.message !== undefined) ? String(e.message) : String(e); }
function cap(p, ms) {
  let t; const timer = new Promise((_, rej) => { t = realST(() => rej(new Error('driver cap: did not settle in ' + ms + ' ms')), ms); });
  return Promise.race([p, timer]).finally(() => realCT(t));
}
async function flush() {
  for (let i = 0; i < 25; i++) {
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => realST(r, 1));
  }
}
const promptCount = (calls) => calls.filter((c) => c.fn === 'session.prompt' || c.fn === 'session.promptAsync').length;

function makeClient(st, calls) {
  const rec = (name, o) => { calls.push({ fn: name, opts: sn(o) }); };
  const noop = (name) => async (o) => { rec(name, o); return { data: {} }; };
  const get = (opts) => {
    rec('session.get', opts);
    const id = opts && opts.path && opts.path.id;
    const e = (id in st.table) ? st.table[id] : st.dflt;
    if (e && e.$throw) throw new Error('session.get threw synchronously');
    if (e && e.$reject) return Promise.reject(new Error('session.get rejected'));
    return Promise.resolve(e);
  };
  const mkPrompt = (name) => (o) => {
    rec(name, o);
    const m = st.prompt.mode;
    if (m === 'throw') throw new Error(name + ' threw synchronously');
    if (m === 'reject') return Promise.reject(new Error(name + ' rejected'));
    if (m === 'pending') return new Promise((res) => { st.pending.push(() => res({ data: {} })); });
    return (async () => { if (st.prompt.echo) await st.echo(o); return { data: {} }; })();
  };
  return { app: { log: noop('app.log') }, session: { get, prompt: mkPrompt('session.prompt'), promptAsync: mkPrompt('session.promptAsync'),
    list: noop('session.list'), abort: noop('session.abort') }, tui: { showToast: noop('tui.showToast') } };
}

async function doChat(hooks, step) {
  const handler = hooks['chat.message'];
  if (typeof handler !== 'function') return { status: 'nohandler' };
  let input, output;
  try { input = build(step.input); output = build(step.output); } catch (e) { return { status: 'driver-error', message: String(e) }; }
  const r = {};
  try {
    await cap(Promise.resolve(handler.call(hooks, input, output)), 60000);
    r.status = 'resolved';
  } catch (e) { r.status = 'threw'; r.message = msgOf(e); }
  return r;
}
async function doGuard(hooks, step) {
  const handler = hooks['tool.execute.before'];
  if (typeof handler !== 'function') return { status: 'nohandler' };
  const input = build(step.input), output = build(step.output);
  const r = {};
  try { await cap(Promise.resolve(handler.call(hooks, input, output)), 60000); r.status = 'resolved'; }
  catch (e) { r.status = 'threw'; r.message = msgOf(e); }
  return r;
}
async function doSeam(hooks, rr, step) {
  if (!rr.seam) return { status: 'noseam' };
  try {
    const v = await cap(Promise.resolve(hooks[rr.seam].call(hooks, build(step.reason))), 30000);
    if (step.save) vars[step.save] = v;
    return { status: 'resolved', text: v, type: typeof v };
  } catch (e) { return { status: 'threw', message: msgOf(e) }; }
}
function growth(calls, before, flag) {
  return new Promise((resolve) => {
    const tick = () => {
      if (flag.done) return resolve({ s: 'stopped' });
      if (promptCount(calls) > before) return resolve({ s: 'held' });
      realST(tick, 2);
    };
    tick();
  });
}
async function doEvent(hooks, step, st, calls) {
  const h = hooks.event;
  if (typeof h !== 'function') return { status: 'nohandler' };
  let arg;
  try {
    arg = ('arg' in step) ? build(step.arg) : { event: { id: 'evt_' + (evn++), type: step.type, properties: build(step.props) } };
  } catch (e) { return { status: 'driver-error', message: String(e) }; }
  const before = promptCount(calls);
  const r = {};
  let p;
  try { p = h.call(hooks, arg); } catch (e) { r.status = 'threw'; r.message = msgOf(e); r.sync = true; return r; }
  r.thenable = !!(p && typeof p.then === 'function');
  const settle = Promise.resolve(p).then(() => ({ s: 'resolved' }), (e) => ({ s: 'threw', m: msgOf(e) }));
  st.open.push(settle);
  const flag = { done: false };
  const racers = [settle];
  if (step.hold) racers.push(growth(calls, before, flag));
  let res;
  try { res = await cap(Promise.race(racers), step.capMs || 30000); } catch (e) { res = { s: 'timeout', m: msgOf(e) }; }
  flag.done = true;
  r.status = res.s; if (res.m) r.message = res.m;
  return r;
}
function doExec(step) {
  const r = spawnSync(step.argv[0], step.argv.slice(1), { input: step.stdin || '', cwd: scen.dir, encoding: 'utf8', timeout: 120000 });
  return { status: 'resolved', rc: r.status, stdout: (r.stdout || '').slice(-600), stderr: (r.stderr || '').slice(-600) };
}

const out = [];
for (const run of scen.runs) {
  const calls = [];
  const st = { table: (run.get && run.get.table) || {}, dflt: (run.get && 'default' in run.get) ? run.get.default : { error: { name: 'NotFoundError' } },
    prompt: Object.assign({ mode: 'resolve', echo: false }, run.prompt || {}), pending: [], open: [], echoes: [], echoN: 0, hooks: null };
  st.echo = async (o) => {
    const sid = o && o.path && o.path.id;
    const parts = (o && o.body && o.body.parts) || [];
    const n = ++st.echoN, mid = 'msg_echo_' + n;
    const h = st.hooks && st.hooks['chat.message'];
    if (typeof h !== 'function') { st.echoes.push({ status: 'nohandler' }); return; }
    const input = { sessionID: sid, messageID: mid, agent: 'build', model: { providerID: 'p', modelID: 'm' } };
    const output = { message: { id: mid, sessionID: sid, role: 'user', time: { created: 1 } },
      parts: parts.map((p, i) => ({ id: 'prt_echo_' + n + '_' + i, sessionID: sid, messageID: mid, type: p.type, text: p.text })) };
    try { await cap(Promise.resolve(h.call(st.hooks, input, output)), 60000); st.echoes.push({ status: 'resolved', sid }); }
    catch (e) { st.echoes.push({ status: 'threw', message: msgOf(e) }); }
  };
  const rr = { label: run.label, factory: 'unknown', hookNames: [], seam: null, steps: [], calls, echoes: st.echoes };
  let hooks = null;
  try {
    const mod = await import(pathToFileURL(run.plugin).href + '?load=' + (loads++));
    if (typeof mod.RatchetPlugin !== 'function') throw new Error('no exported RatchetPlugin function');
    const ctx = { client: makeClient(st, calls), directory: scen.dir, worktree: scen.dir, project: { id: 'p' }, serverUrl: 'http://localhost:4096' };
    hooks = await mod.RatchetPlugin(ctx);
    st.hooks = hooks;
    rr.factory = 'ok';
    rr.hookNames = (hooks && typeof hooks === 'object') ? Object.keys(hooks) : [];
    for (const k of rr.hookNames) {
      if (typeof hooks[k] !== 'function' || KNOWN.has(k) || k.startsWith('experimental.')) continue;
      try {
        const v = await cap(Promise.resolve(hooks[k].call(hooks, 'probe')), 30000);
        if (typeof v === 'string' && v.includes(scen.prefix)) { rr.seam = k; break; }
      } catch (e) { void e; }
    }
  } catch (e) { rr.factory = 'threw: ' + msgOf(e); }
  for (const step of run.steps) {
    let res;
    if (!hooks || typeof hooks !== 'object') res = { status: 'nohooks' };
    else {
      try {
        if (step.op === 'event') res = await doEvent(hooks, step, st, calls);
        else if (step.op === 'chat') res = await doChat(hooks, step);
        else if (step.op === 'guard') res = await doGuard(hooks, step);
        else if (step.op === 'seam') res = await doSeam(hooks, rr, step);
        else if (step.op === 'exec') res = doExec(step);
        else if (step.op === 'release') {
          const fns = st.pending.splice(0); fns.forEach((f) => f());
          await cap(Promise.all(st.open), 30000).catch(() => {});
          res = { status: 'resolved', released: fns.length };
        } else if (step.op === 'setPrompt') { Object.assign(st.prompt, step.set); res = { status: 'resolved' }; }
        else if (step.op === 'timers') res = { status: 'resolved', pending: timers.filter((t) => !t.done).map((t) => t.ms) };
        else if (step.op === 'fireTimers') {
          const todo = timers.filter((t) => !t.done); todo.forEach((t) => { t.done = true; realCT(t.h); });
          for (const t of todo) { try { if (typeof t.fn === 'function') t.fn(...t.args); } catch (e) { void e; } }
          await flush();
          res = { status: 'resolved', fired: todo.length };
        } else res = { status: 'driver-error', message: 'unknown op ' + step.op };
      } catch (e) { res = { status: 'driver-error', message: msgOf(e) }; }
    }
    res.n = promptCount(calls);
    rr.steps.push(res);
  }
  await flush();
  rr.finalN = promptCount(calls);
  out.push(rr);
}
await flush();
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

    def view(self, i):
        """A single-run Out holding only run `i` of this one (the spy log stays shared)."""
        import copy
        v = copy.copy(self)
        v.data = {"runs": [self.run(i)], "problems": self.problems}
        return v

    def run(self, i=0):
        runs = (self.data or {}).get("runs") or []
        return runs[i] if i < len(runs) else {"factory": "missing", "hookNames": [], "steps": [], "calls": [],
                                              "echoes": []}

    def step(self, i=0, j=0):
        ss = self.run(i).get("steps") or []
        return ss[j] if j < len(ss) else {"status": "missing"}

    def statuses(self, i=0):
        return [s.get("status") for s in self.run(i).get("steps") or []]

    def ns(self, i=0):
        """Cumulative prompt-call count after each step."""
        return [s.get("n") for s in self.run(i).get("steps") or []]

    def loaded(self, i=0, hook="event"):
        return self.run(i).get("factory") == "ok" and hook in (self.run(i).get("hookNames") or [])

    def seam(self, i=0):
        return self.run(i).get("seam")

    def clean(self):
        """The node process exited normally and reported no unhandled rejection or uncaught exception."""
        return self.rc == 0 and self.data is not None and self.problems == []

    def final_n(self, i=0):
        return self.run(i).get("finalN")

    def sent(self, i=0):
        """Every prompt call the plugin made: {fn, sid, text, noReply, nparts}; `text` is the single text part's text."""
        res = []
        for c in self.run(i).get("calls") or []:
            if c.get("fn") not in ("session.prompt", "session.promptAsync"):
                continue
            opts = c.get("opts") if isinstance(c.get("opts"), dict) else {}
            path = opts.get("path") if isinstance(opts.get("path"), dict) else {}
            body = opts.get("body") if isinstance(opts.get("body"), dict) else {}
            parts = body.get("parts") if isinstance(body.get("parts"), list) else []
            ok = len(parts) == 1 and isinstance(parts[0], dict) and parts[0].get("type") == "text" \
                and isinstance(parts[0].get("text"), str)
            res.append({"fn": c["fn"], "sid": path.get("id"), "text": parts[0]["text"] if ok else None,
                        "noReply": body.get("noReply"), "nparts": len(parts)})
        return res

    def sids(self, i=0):
        return [p["sid"] for p in self.sent(i)]

    def engine_calls(self, event):
        """Every `hook <event>` engine call the plugin made: (payload dict or None, reply dict or None, record)."""
        res = []
        for rec in self.spy:
            argv = rec.get("argv") or []
            if "hook" in argv and event in argv:
                try:
                    pl = json.loads(rec.get("stdin") or "")
                except ValueError:
                    pl = None
                try:
                    rp = json.loads(rec.get("stdout") or "")
                except ValueError:
                    rp = None
                res.append((pl, rp, rec))
        return res


def drive(w, runs, env_extra=None, timeout=300):
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


def top(sid):
    return {"data": {"id": sid}}


TABLE = {s: top(s) for s in ("ses_main", "ses_other", "ses_a", "ses_b", "ses_owner", "ses_visitor")}
TABLE["ses_child"] = {"data": {"id": "ses_child", "parentID": "ses_main"}}


def run_of(plugin, steps, label="run", table=None, prompt=None):
    return {"label": label, "plugin": str(plugin), "steps": steps,
            "get": {"table": table if table is not None else TABLE, "default": {"error": {"name": "NotFoundError"}}},
            "prompt": prompt or {"mode": "resolve", "echo": False}}


def idle(sid="ses_main", **kw):
    return {"op": "event", "type": "session.idle", "props": {"sessionID": sid}, **kw}


def ev(typ, props=None, **kw):
    return {"op": "event", "type": typ, "props": props if props is not None else {}, **kw}


def abort_ev(sid="ses_main"):
    return ev("session.error", {"sessionID": sid, "error": {"name": "MessageAbortedError",
                                                             "data": {"message": "The operation was aborted."}}})


def raw_event(arg_js):
    return {"op": "event", "arg": {"$js": arg_js}}


_pid = [0]
_mid = [0]


def part(text, sid="ses_main", mid="msg_1", **kw):
    _pid[0] += 1
    p = {"id": f"prt_{_pid[0]}", "type": "text", "text": text}
    if sid is not None:
        p["sessionID"] = sid
    if mid is not None:
        p["messageID"] = mid
    p.update(kw)
    return p


def chat(parts, sid="ses_main", mid="msg_1", **kw):
    """One chat.message call exactly as OpenCode makes it: hook(input{sessionID, messageID, agent, model}, output{message, parts})."""
    inp = {"agent": "build", "model": {"providerID": "p", "modelID": "m"}, "sessionID": sid, "messageID": mid}
    msg = {"role": "user", "time": {"created": 1}, "sessionID": sid, "id": mid}
    return {"op": "chat", "input": inp, "output": {"message": msg, "parts": parts}, **kw}


def typed(text, sid="ses_main", mid=None, **kw):
    if mid is None:
        _mid[0] += 1
        mid = f"msg_t{_mid[0]}"
    return chat([part(text, sid, mid)], sid, mid, **kw)


def seam_step(reason="the reason", save=None):
    return {"op": "seam", "reason": reason, **({"save": save} if save else {})}


def var(name, *path):
    return {"$var": name, "path": list(path)}


def guard_step(tool, args, sid="ses_main", cid="call_1"):
    return {"op": "guard", "input": {"tool": tool, "sessionID": sid, "callID": cid}, "output": {"args": args}}


def exec_step(*argv):
    return {"op": "exec", "argv": list(argv)}


# --------------------------------------------------------------------------------------- stand-in engines

STUB_BODY = r'''
import json, os, sys
LOG, CNT, CFG = %(log)r, %(cnt)r, %(cfg)r
def note(**kw):
    with open(LOG, 'a') as f:
        f.write(json.dumps(kw) + '\n')
data = sys.stdin.read()
ev = sys.argv[2] if len(sys.argv) > 2 else ''
try:
    pl = json.loads(data)
except ValueError:
    pl = {}
note(argv=sys.argv[1:], stdin=data)
if ev != 'stop':
    print(json.dumps({'block': False, 'message': ''}))
    sys.exit(0)
try:
    n = int(open(CNT).read())
except Exception:
    n = 0
open(CNT, 'w').write(str(n + 1))
kind = CFG['kind']
if kind == 'const':
    print(json.dumps({'block': True, 'message': CFG['reason']}))
elif kind == 'uniq':
    print(json.dumps({'block': True, 'message': 'RATCHET GATE: stub reason %%d\nNext required action: stub step %%d' %% (n, n)}))
elif kind == 'seq':
    rs = CFG['reasons']
    print(json.dumps({'block': True, 'message': rs[min(n, len(rs) - 1)]}))
elif kind == 'owner':
    sid = pl.get('session_id')
    bound = sid is None or sid == CFG['owner']
    print(json.dumps({'block': bound, 'message': CFG['reason'] if bound else ''}))
elif kind == 'wait':
    print(json.dumps({'block': False, 'message': 'RATCHET: waiting on a stub task, so this stop is allowed and not counted.'}))
elif kind == 'reply':
    print(json.dumps(CFG['reply']))
elif kind == 'raw':
    sys.stdout.write(CFG['stdout'])
    sys.stderr.write(CFG['stderr'])
    sys.exit(CFG['code'])
'''

STUB_REASON = "RATCHET GATE: stub gate is open\nNext required action: finish the stub step"


def stub_engine(w, cfg):
    """A stand-in engine script: answers `hook stop` per `cfg` (a dict, see STUB_BODY) and every other hook with
    {block:false, message:''}. Returns the script path."""
    d = w.s.work / "stubs"
    d.mkdir(exist_ok=True)
    script = d / f"{w.s.next('stub')}.py"
    log, cnt = d / f"{script.stem}.log", d / f"{script.stem}.cnt"
    script.write_text(STUB_BODY % {"log": str(log), "cnt": str(cnt), "cfg": cfg}, encoding="utf-8")
    return script


def const_stub(w, reason=STUB_REASON):
    return stub_engine(w, {"kind": "const", "reason": reason})


_STR_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def retargeted_plugin(w, stem, engine_path, src=None):
    """A copy of the rendered plugin, in the work folder, whose engine path (a JSON string literal) is `engine_path`.
    Returns (path to the copy, how many literals were replaced)."""
    text = Path(src or real_plugin()).read_text(encoding="utf-8")
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


def stubbed(w, cfg, stem="stubbed"):
    """The REAL rendered plugin pointed at a stand-in engine. Returns (plugin copy, stub script, literals replaced)."""
    stub = stub_engine(w, cfg)
    p, n = retargeted_plugin(w, stem, stub)
    return p, stub, n


# ------------------------------------------------------------------------- reference stop plugin and its mutants

REF_JS = r'''
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
const ENGINE = __ENGINE__;
const O = __OPTS__;
const PREFIX = "[RATCHET PLUGIN TURN - NOT USER INPUT]";
const STOP = "hook stop --host opencode".split(" ");
const PROMPT = "hook prompt --host opencode".split(" ");
function ask(args, payload) {
  return new Promise((resolve) => {
    let out = "", done = false;
    const fin = (v) => { if (!done) { done = true; resolve(v); } };
    try {
      const c = spawn("python3", [ENGINE, ...args], { stdio: ["pipe", "pipe", "ignore"] });
      c.on("error", () => fin(null));
      c.stdin.on("error", () => {});
      c.stdout.on("data", (d) => { out += d; });
      c.on("close", (code) => fin(code === 0 ? out : null));
      c.stdin.end(payload);
    } catch (e) { fin(null); }
  });
}
function reasonOf(out) {
  if (typeof out !== "string") return null;
  let r;
  try { r = JSON.parse(out); } catch (e) { return null; }
  if (!r || typeof r !== "object" || Array.isArray(r)) return null;
  const blocked = O.loose_flag ? !!r.block : r.block === true;
  if (O.honor_flag && !blocked) return null;
  if (!O.honor_flag && !r.message) return null;
  if (O.strict_msg) return typeof r.message === "string" && r.message.trim() !== "" ? r.message : null;
  return typeof r.message === "string" ? r.message : String(r.message);
}
export const RatchetPlugin = async (ctx) => {
  const project = (ctx && (ctx.directory || ctx.worktree)) || process.cwd();
  const token = randomUUID();
  const S = new Map(), aborted = new Set(), flying = new Set();
  let abortedAll = false;
  const sess = (sid) => {
    const k = O.per_session ? sid : "*";
    if (!S.has(k)) S.set(k, { last: null, row: 0, total: 0 });
    return S.get(k);
  };
  const isTop = async (sid) => {
    try {
      const g = await ctx.client.session.get({ path: { id: sid } });
      if (!g || typeof g !== "object" || !g.data || typeof g.data !== "object") return O.lookup_fail_open;
      const p = g.data.parentID;
      return p === undefined || p === null || p === "";
    } catch (e) { return O.lookup_fail_open; }
  };
  const payload = (sid) => JSON.stringify(O.session_in_payload
    ? { hook_event_name: "session.idle", session_id: sid, cwd: project }
    : { hook_event_name: "session.idle", cwd: project });
  const send = (sid, text) => ctx.client.session.prompt({ path: { id: sid }, body: { parts: [{ type: "text", text }] } });
  const onIdle = async (sid) => {
    if (O.skip_abort && (abortedAll || aborted.has(sid))) return;
    const fk = O.inflight_per_session ? sid : "*";
    if (O.inflight && flying.has(fk)) { if (O.count_ignored) sess(sid).row++; return; }
    const s0 = sess(sid);
    if (O.cap_row && s0.last !== null && s0.row >= 3) return;   // capped: stay silent and do not ask the engine again
    if (O.ceiling && s0.total >= 20) return;
    if (O.inflight) flying.add(fk);
    try {
      let ans = null;
      if (O.engine_first) ans = await ask(STOP, payload(sid));
      if (O.skip_child && !(await isTop(sid))) return;
      if (!O.engine_first) ans = await ask(STOP, payload(sid));
      const msg = reasonOf(ans);
      if (msg === null) return;
      const s = sess(sid);
      if (O.cap_row) {
        if (s.last === msg) { if (s.row >= 3) return; s.row++; }
        else { s.last = msg; s.row = O.reset_row_on_change ? 1 : s.row + 1; if (s.row > 3) return; }
      }
      if (O.ceiling && s.total >= 20) return;
      s.total++;
      const text = O.marker ? PREFIX + " " + token + "\n" + msg : msg;
      try { await send(sid, text); }
      catch (e) {
        if (O.retry_inline) { try { await send(sid, text); } catch (e2) { void e2; } }
        if (O.retry_timer) setTimeout(() => { try { Promise.resolve(send(sid, text)).catch(() => {}); } catch (e3) { void e3; } }, 5000);
        if (O.throw_out) throw e;
      }
    } finally { if (O.clear_inflight) flying.delete(fk); }
  };
  const onEvent = async (arg) => {
    let type, props, sid;
    if (O.guard_event) {
      const e = arg && arg.event;
      if (!e || typeof e !== "object") return;
      type = e.type; props = e.properties;
      if (!props || typeof props !== "object") return;
      sid = props.sessionID;
      if (typeof sid !== "string" || sid === "") return;
    } else {
      type = arg.event.type; props = arg.event.properties; sid = props.sessionID;
    }
    if (type === "session.error") {
      const er = props.error;
      if (er && er.name === "MessageAbortedError") { if (O.abort_global) abortedAll = true; else aborted.add(sid); }
      return;
    }
    if (type === "session.idle" || O.react_any || (O.react_status_idle && type === "session.status" && props.status && props.status.type === "idle")) {
      await onIdle(sid);
    }
  };
  const reset = (sid) => {
    if (!O.reset_typed) return;
    sess(sid);
    for (const s of (O.reset_all ? [...S.values()] : [sess(sid)])) {
      s.row = 0; s.last = null;
      if (O.reset_ceiling) s.total = 0;
    }
    if (O.abort_clear) { if (O.reset_all) aborted.clear(); else aborted.delete(sid); abortedAll = false; }
  };
  return {
    ratchetMarkedText: (reason) => PREFIX + " " + token + "\n" + reason,
    event: async (arg) => { try { await onEvent(arg); } catch (e) { if (O.throw_out) throw e; } },
    "tool.execute.before": async () => {},
    "chat.message": async (input, output) => {
      try {
        if (O.stop_in_chat) await ask(STOP, payload(input && input.sessionID));
        const sid = input && input.sessionID, parts = output && output.parts;
        if (typeof sid !== "string" || !Array.isArray(parts)) return;
        if (O.abort_clear_any) aborted.delete(sid);
        const text = parts.filter((p) => p && p.type === "text" && !p.synthetic && typeof p.text === "string").map((p) => p.text).join("\n");
        if (!text.trim()) return;
        if (O.filt_token && text.includes(token)) return;
        if (O.filt_prefix && text.split("\n")[0].startsWith(PREFIX)) return;
        if (O.filt_child && !(await isTop(sid))) return;
        await ask(PROMPT, JSON.stringify({ hook_event_name: "chat.message", session_id: sid, message_id: input.messageID, cwd: project, prompt: text }));
        reset(sid);
      } catch (e) { void e; }
    },
  };
};
'''

GOOD = dict(marker=True, cap_row=True, ceiling=True, per_session=True, inflight=True, inflight_per_session=True,
            clear_inflight=True, reset_row_on_change=True, count_ignored=False, reset_typed=True, reset_ceiling=True,
            filt_token=True, filt_prefix=True, filt_child=True, abort_clear_any=False, abort_clear=True,
            skip_child=True, lookup_fail_open=False, engine_first=False, skip_abort=True, abort_global=False,
            throw_out=False, guard_event=True, retry_inline=False, retry_timer=False, honor_flag=True,
            loose_flag=False, strict_msg=True, session_in_payload=True, stop_in_chat=False, react_status_idle=False,
            react_any=False, reset_all=False)


def ref_plugin(w, engine, **over):
    """The reference stop plugin (everything right) with `over` options changed, pointed at `engine`."""
    bad = [k for k in over if k not in GOOD]
    assert not bad, bad
    src = REF_JS.replace("__ENGINE__", json.dumps(str(engine))).replace("__OPTS__", json.dumps({**GOOD, **over}))
    d = w.s.work / "refs"
    d.mkdir(exist_ok=True)
    p = d / f"{w.s.next('ref')}.js"
    p.write_text(src, encoding="utf-8")
    return p


# ------------------------------------------------------------------------------------------- verdict helpers

def ok_all(res):
    return all(c for _l, c, _d in res)


def emit(cid, res):
    for label, cond, detail in res:
        check(f"[{cid}] {label}", cond, detail)


def tooth(cid, label, res, expect_ok):
    """A teeth check: the verdict `res` must come out OK for the reference and NOT OK for a mutant."""
    bad = [l for l, c, _d in res if not c]
    if expect_ok:
        check(f"[{cid}] teeth: {label} passes the checker", ok_all(res), bad)
    else:
        check(f"[{cid}] teeth: {label} is refused by the checker", not ok_all(res), "the checker accepted a wrong plugin")


def first_line(t):
    return t.split("\n", 1)[0] if isinstance(t, str) else ""


def marker_token(t):
    fl = first_line(t)
    if not fl.startswith(PREFIX) or len(fl) > 200:
        return None
    toks = re.findall(r"[0-9A-Za-z-]{20,}", fl[len(PREFIX):])
    return max(toks, key=len) if toks else None


def short(x):
    return json.dumps(x)[:260] if isinstance(x, (dict, list)) else repr(x)[:260]


def all_resolved(o, i=0):
    return all(s in ("resolved",) for s in o.statuses(i))


def stub_world():
    """A project with no Ratchet run: where a stand-in engine answers and the plugin is only handed its directory."""
    return fresh("bare")


# ---------------------------------------------------------------------------------- engine-side reads (real engine)

def log_entries(w):
    p = w.proj / ".ratchet" / "human_log.jsonl"
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def run_state(w):
    try:
        return json.loads((w.run_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stop_block_records(w):
    p = w.run_dir / "stop-blocks.jsonl"
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def engine_stop(w, sid="ses_main"):
    r = w.sh("hook", "stop", "--host", "opencode", stdin=json.dumps({"hook_event_name": "session.idle",
                                                                      "session_id": sid, "cwd": str(w.proj)}))
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"unparsed": r.stdout[-200:], "rc": r.returncode}


def hook_errors(w):
    p = w.proj / ".ratchet" / "hook-errors.log"
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ------------------------------------------------------------------------------------------------------ the cases

# ---- T01

def v_t01(o):
    r = o.sent()
    seam_text = o.step(0, 0).get("text")
    text = r[0]["text"] if r else None
    stops = o.engine_calls("stop")
    reply = stops[0][1] if stops else None
    msg = reply.get("message") if isinstance(reply, dict) else None
    rest = text.split("\n", 1)[1] if isinstance(text, str) and "\n" in text else ""
    return [
        ("setup: the plugin loads under node and has an event hook; nothing crashed",
         o.loaded() and o.clean(), (o.run().get("factory"), o.run().get("hookNames"), o.problems)),
        ("the idle event's handler resolves", o.statuses()[-1:] == ["resolved"], o.statuses()),
        ("exactly one prompt call is made, and no other session is prompted", len(r) == 1 and o.sids() == ["ses_main"],
         o.sids()),
        ("it is one text part, a real turn (noReply is not true)", len(r) == 1 and r[0]["nparts"] == 1
         and r[0]["text"] is not None and r[0]["noReply"] is not True, r),
        ("the first line of the text is the plugin's own marker line (the very line its seam makes for this load)",
         isinstance(text, str) and marker_token(text) is not None and first_line(text) == first_line(seam_text),
         (first_line(text), first_line(seam_text))),
        ("the rest of the text holds the engine's reason ('RATCHET GATE' and 'Next required action')",
         "RATCHET GATE" in rest and "Next required action" in rest, rest[:200]),
        ("it is the engine's own reply that was delivered (the stop hook said block with exactly this message)",
         isinstance(reply, dict) and reply.get("block") is True and isinstance(msg, str) and msg.strip() in rest,
         short(reply)),
        ("the plugin asked `hook stop --host opencode` once, with this session's id and a project folder",
         len(stops) == 1 and isinstance(stops[0][0], dict) and stops[0][0].get("session_id") == "ses_main"
         and isinstance(stops[0][0].get("cwd"), str) and stops[0][0]["cwd"] != "", [s[0] for s in stops]),
    ]


def case_t01():
    print("[CP6-T01] an open gate sends the agent back to work with the reason")
    w = fresh()
    o = drive(w, [run_of(w.plugin, [seam_step("probe", "mk"), idle()])])
    res = v_t01(o)
    stops = o.engine_calls("stop")
    res.append(("the stop payload's cwd is the project folder",
                len(stops) == 1 and isinstance(stops[0][0], dict) and stops[0][0].get("cwd") == str(w.proj),
                [s[0] for s in stops]))
    emit("CP6-T01", res)
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True), ("a plugin that sends the reason with no marker line",
                                                                {"marker": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), [seam_step("probe", "mk"), idle()])])
        tooth("CP6-T01", name, v_t01(o2), expect)


# ---- T02

def v_t02(o, w, log_before):
    stops = o.engine_calls("stop")
    replies = [s[1] for s in stops]
    blocks = [r for r in stop_block_records(w) if not r.get("reset")]
    echoes = o.run().get("echoes") or []
    st = run_state(w)
    log_after = log_entries(w)
    return [
        ("setup: the plugin loads and the idle handlers resolve", o.loaded() and o.clean() and all_resolved(o),
         (o.statuses(), o.problems)),
        ("setup: the harness fed each re-prompt back to chat.message as a user-role message (two echoes)",
         len(echoes) == 2 and all(e.get("status") == "resolved" for e in echoes), echoes),
        ("exactly 2 re-prompts are sent over four idles (counts after each idle: 1, 2, 2, 2)", o.ns() == [1, 2, 2, 2],
         o.ns()),
        ("the engine's first two stop answers are blocks and the third is its safety valve (not a block)",
         len(replies) >= 3 and isinstance(replies[0], dict) and replies[0].get("block") is True
         and replies[1].get("block") is True and replies[2].get("block") is False
         and "safety valve" in str(replies[2].get("message")), [short(r) for r in replies[:4]]),
        ("the third idle sends nothing: the plugin does not talk over the valve", o.ns()[2] == 2 and len(o.sent()) == 2,
         o.ns()),
        ("the run is marked blocked for the user", st.get("phase") == "blocked", st.get("phase")),
        ("the engine's stop-block count kept growing across the echoed messages (three blocked stops on record)",
         len(blocks) >= 3, len(blocks)),
        ("the echoed messages recorded no human reply (human_log.jsonl is exactly as before, no marker text in it)",
         log_after == log_before and not any(PREFIX in json.dumps(e) for e in log_after),
         (len(log_before), len(log_after))),
    ]


def case_t02():
    print("[CP6-T02] the plugin's own re-prompt never counts as the user typing, so the engine's limit still halts the loop")
    steps = [idle(), idle(), idle(), idle()]
    echo = {"mode": "resolve", "echo": True}
    w = fresh(max_stop_blocks=2)
    before = log_entries(w)
    o = drive(w, [run_of(w.plugin, steps, prompt=echo)])
    check("[CP6-T02] setup: the engine's max_stop_blocks is 2, below the plugin's own cap of 3",
          engine_stop(fresh(max_stop_blocks=2)).get("block") is True and before != [])
    emit("CP6-T02", v_t02(o, w, before))
    # teeth: the reference passes; a mutant that records the echo as typed is caught; a mutant without the plugin's
    # own caps is still halted by the engine at 2 (the second line works alone); without both lines it never stops.
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that records its own re-prompt as typed (no marker)", {"marker": False}, False),
                               ("a plugin with no caps of its own: the engine alone halts it at 2",
                                {"cap_row": False, "ceiling": False}, True)):
        w2 = fresh(max_stop_blocks=2)
        b2 = log_entries(w2)
        o2 = drive(w2, [run_of(ref_plugin(w2, ENGINE, **opts), steps, prompt=echo)])
        tooth("CP6-T02", name, v_t02(o2, w2, b2), expect)
    w3 = fresh(max_stop_blocks=2)
    b3 = log_entries(w3)
    o3 = drive(w3, [run_of(ref_plugin(w3, ENGINE, marker=False, cap_row=False, ceiling=False), steps, prompt=echo)])
    check("[CP6-T02] teeth: with no marker and no caps the loop is unbounded (all four idles re-prompt), so the engine "
          "coupling above is what bounds it", o3.ns() == [1, 2, 3, 4], o3.ns())


# ---- T03

def make_waiting(w, sid="ses_main"):
    """The owner chat said it is waiting on background work: its typed message owns the run, its `wait` call is noted
    by the guard, then `ratchet.py wait`."""
    pl = {"hook_event_name": "chat.message", "session_id": sid, "cwd": str(w.proj), "prompt": "go on please",
          "message_id": "msg_w1"}
    _must(w.sh("hook", "prompt", "--host", "opencode", stdin=json.dumps(pl)))
    cmd = f'python3 {ENGINE} wait --for "a background build"'
    _must(w.sh("hook", "guard", "--host", "opencode", stdin=json.dumps(
        {"tool_name": "bash", "tool_input": {"command": cmd}, "cwd": str(w.proj), "session_id": sid})))
    _must(w.sh("wait", "--for", "a background build"))


def v_t03(o, label, w=None):
    stops = o.engine_calls("stop")
    res = [(f"{label}: the plugin loads, the handler resolves, nothing crashed",
            o.loaded() and o.clean() and o.statuses() == ["resolved"], (o.statuses(), o.problems)),
           (f"{label}: no prompt call is made", o.ns() == [0] and o.sent() == [], o.ns())]
    if w is not None:
        res.append((f"{label}: nothing is written to the project's hook-errors log", hook_errors(w) == "",
                    hook_errors(w)[:200]))
    return res, stops


def case_t03():
    print("[CP6-T03] nothing to block means no re-prompt")
    plugin = real_plugin()
    wa = fresh("bare")
    oa = drive(wa, [run_of(plugin, [idle()])])
    ra, _s = v_t03(oa, "(a) no Ratchet run at all", wa)
    emit("CP6-T03", ra)
    wb = fresh()
    _must(wb.sh("pause", "--reason", "taking a break"))
    ob = drive(wb, [run_of(wb.plugin, [idle()])])
    rb, sb = v_t03(ob, "(b) a paused run", wb)
    rb.append(("(b) setup: the engine's stop answer was 'allow' with an empty message",
               len(sb) == 1 and sb[0][1] == {"block": False, "message": ""}, [x[1] for x in sb]))
    emit("CP6-T03", rb)
    wc = fresh()
    make_waiting(wc)
    oc = drive(wc, [run_of(wc.plugin, [idle()])])
    rc, sc = v_t03(oc, "(c) a run with an active wait", wc)
    rc.append(("(c) setup: the engine said allow WITH a message ('waiting on ...'): the flag, not the text, decides",
               len(sc) == 1 and isinstance(sc[0][1], dict) and sc[0][1].get("block") is False
               and str(sc[0][1].get("message")).startswith("RATCHET: waiting on"), [x[1] for x in sc]))
    emit("CP6-T03", rc)
    sw = stub_world()
    stub = stub_engine(sw, {"kind": "wait"})
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that sends whenever the engine's message is not empty", {"honor_flag": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), [idle()])])
        tooth("CP6-T03", name, v_t03(o2, "wait")[0], expect)


# ---- T04

def v_t04(o, label="open gate"):
    texts = [p["text"] for p in o.sent()]
    reasons = {t.split("\n", 1)[1] for t in texts if isinstance(t, str) and "\n" in t}
    return [(f"{label}: the plugin loads and all six idle handlers resolve without throwing",
             o.loaded() and o.clean() and o.statuses() == ["resolved"] * 6, (o.statuses(), o.problems)),
            (f"{label}: exactly 3 re-prompts are made, one per idle for the first three (counts 1, 2, 3, 3, 3, 3)",
             o.ns() == [1, 2, 3, 3, 3, 3], o.ns()),
            (f"{label}: all three carry the same reason", len(texts) == 3 and len(reasons) == 1, [str(t)[:60] for t in texts])]


def case_t04():
    print("[CP6-T04] three re-prompts in a row with no progress, then it stops sending")
    w = fresh()
    o = drive(w, [run_of(w.plugin, [idle() for _ in range(6)], prompt={"mode": "resolve", "echo": True})])
    emit("CP6-T04", v_t04(o))
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin with no caps", {"cap_row": False, "ceiling": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), [idle() for _ in range(6)])])
        tooth("CP6-T04", name, v_t04(o2, name), expect)


# ---- T05

def v_t05(o):
    texts = [p["text"] or "" for p in o.sent()]
    return [("setup: the plugin loads, every step resolves, nothing crashed",
             o.loaded() and o.clean() and all_resolved(o), (o.statuses(), o.problems)),
            ("setup: the real command that moves the run ran and succeeded", o.step(0, 2).get("rc") == 0,
             short(o.step(0, 2))),
            ("two re-prompts before the move, a new one right after it, 3 for the new reason, none after "
             "(2 + 3 = 5 prompts overall)", o.ns() == [1, 2, 2, 3, 4, 5, 5, 5], o.ns()),
            ("the first two carry the old reason", len(texts) >= 2 and all("step=tests" in t for t in texts[:2]),
             [t[:90] for t in texts[:2]]),
            ("the next three carry the new reason, and not the old one",
             len(texts) == 5 and all("step=red" in t and "step=tests" not in t for t in texts[2:]),
             [t[:90] for t in texts[2:]])]


def case_t05():
    print("[CP6-T05] real progress restarts the in-a-row count")
    w = fresh()
    (w.proj / "t.test.js").write_text("it('[CP1-T01] x', () => {})\n")
    steps = [idle(), idle(), exec_step(sys.executable, str(ENGINE), "tests-written", "CP1")] + [idle() for _ in range(5)]
    o = drive(w, [run_of(w.plugin, steps, prompt={"mode": "resolve", "echo": True})])
    emit("CP6-T05", v_t05(o))
    sw = stub_world()
    a, b = "RATCHET GATE: A\nstep=tests", "RATCHET GATE: B\nstep=red"
    stub = stub_engine(sw, {"kind": "seq", "reasons": [a, a, b]})
    steps2 = [idle(), idle(), exec_step(sys.executable, "-c", "pass")] + [idle() for _ in range(5)]
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin whose count does not restart when the reason changes",
                                {"reset_row_on_change": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), steps2)])
        tooth("CP6-T05", name, v_t05(o2), expect)
        # the stub counts calls, so give each variant its own copy of the sequence
        stub = stub_engine(sw, {"kind": "seq", "reasons": [a, a, b]})


# ---- T06

def v_t06(o):
    exp = [min(i + 1, 20) for i in range(30)]
    return [("setup: the plugin loads, all thirty idle handlers resolve, nothing crashed",
             o.loaded() and o.clean() and o.statuses() == ["resolved"] * 30, (o.statuses()[:3], o.problems)),
            ("exactly 20 re-prompts are sent: one per idle for the first 20, nothing for idles 21 to 30", o.ns() == exp,
             o.ns()),
            ("the 20 reasons really did change every time (so the in-a-row cap never applied)",
             len({p["text"].split("\n", 1)[1] for p in o.sent() if p["text"] and "\n" in p["text"]}) == 20, len(o.sent()))]


def case_t06():
    print("[CP6-T06] twenty re-prompts per session is the hard ceiling even when the reason keeps changing")
    w = stub_world()
    p, _stub, n = stubbed(w, {"kind": "uniq"}, "t06")
    check("[CP6-T06] setup: the rendered plugin copy names the engine as a JSON string literal", n >= 1, n)
    o = drive(w, [run_of(p, [idle() for _ in range(30)])])
    emit("CP6-T06", v_t06(o))
    for name, opts, expect in (("reference plugin", {}, True), ("a plugin with no per-session ceiling", {"ceiling": False}, False)):
        stub = stub_engine(w, {"kind": "uniq"})
        o2 = drive(w, [run_of(ref_plugin(w, stub, **opts), [idle() for _ in range(30)])])
        tooth("CP6-T06", name, v_t06(o2), expect)


# ---- T07

def v_t07a(o):
    exp = [1, 2, 3, 3, 3, 4, 5, 6, 6]
    # steps: idle x4, typed, idle x4  -> after the typed message three more are allowed, then the cap holds again
    return [("setup: the plugin loads, every step resolves, nothing crashed", o.loaded() and o.clean() and all_resolved(o),
             (o.statuses(), o.problems)),
            ("at the 3-in-a-row cap nothing is sent before the user types (3 prompts after four idles)", o.ns()[:4] == [1, 2, 3, 3],
             o.ns()),
            ("after the user's typed message the count starts over: the next idle re-prompts and 3 more are allowed, "
             "then the cap holds again", o.ns() == exp, o.ns())]


def v_t07b(o):
    pre = [min(i + 1, 20) for i in range(22)]
    post = [20 + min(i + 1, 20) for i in range(22)]
    ns = o.ns()
    return [("setup: the plugin loads, every step resolves, nothing crashed", o.loaded() and o.clean() and all_resolved(o),
             (o.statuses()[:3], o.problems)),
            ("at the 20-per-session ceiling nothing is sent (22 idles, 20 prompts)", ns[:22] == pre, ns[:22]),
            ("after the typed message the ceiling restarts: exactly 20 more are sent over the next 22 idles",
             ns[22] == 20 and ns[23:] == post, ns[22:])]


def case_t07():
    print("[CP6-T07] only a message the user really typed resets the caps")
    w = fresh()
    steps_a = [idle() for _ in range(4)] + [typed("keep going please")] + [idle() for _ in range(4)]
    o = drive(w, [run_of(w.plugin, steps_a)])
    emit("CP6-T07", v_t07a(o))
    ents = log_entries(w)
    check("[CP6-T07] the typed message was recorded as the user's reply (human_log.jsonl has it)",
          any(e.get("prompt") == "keep going please" for e in ents), [e.get("prompt") for e in ents])
    sw = stub_world()
    p, _stub, n = stubbed(sw, {"kind": "uniq"}, "t07")
    steps_b = [idle() for _ in range(22)] + [typed("carry on")] + [idle() for _ in range(22)]
    ob = drive(sw, [run_of(p, steps_b)])
    emit("CP6-T07", [(f"(20 ceiling) {l}", c, d) for l, c, d in v_t07b(ob)])
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin the user's typed message never resets", {"reset_typed": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), steps_a)])
        tooth("CP6-T07", name, v_t07a(o2), expect)
    for name, opts, expect in (("reference plugin (20 ceiling)", {}, True),
                               ("a plugin whose typed message does not restart the 20 ceiling", {"reset_ceiling": False}, False)):
        stub2 = stub_engine(sw, {"kind": "uniq"})
        o2 = drive(sw, [run_of(ref_plugin(sw, stub2, **opts), steps_b)])
        tooth("CP6-T07", name, v_t07b(o2), expect)


# ---- T08

def t08_steps():
    marker_shaped = f"{PREFIX} {FAKE_TOKEN}\nplease continue with the plan"
    return ([idle() for _ in range(4)]                                     # 0-3: cap reached, 3 prompts
            + [seam_step("the plugin's own re-prompt text", "mk"),         # 4
               chat([part(var("mk"), mid="msg_echo_x")], mid="msg_echo_x"),  # 5: the plugin's own text fed back
               idle(),                                                      # 6
               typed("I am a sub-agent speaking", sid="ses_child"),         # 7: a message from a child session
               idle(),                                                      # 8
               typed(marker_shaped),                                        # 9: marker-shaped text sent as if typed
               idle(),                                                      # 10
               typed("real words from the user"),                           # 11: control: a genuine typed message
               idle()])                                                     # 12


def v_t08(o):
    ns = o.ns()
    return [("setup: the plugin loads, every step resolves, nothing crashed", o.loaded() and o.clean() and all_resolved(o),
             (o.statuses(), o.problems)),
            ("setup: ses_main is at the 3-in-a-row cap (3 prompts after four idles)", ns[:4] == [1, 2, 3, 3], ns[:4]),
            ("the plugin's own re-prompt text fed back through chat.message does not reset the caps (no prompt after it)",
             ns[5:7] == [3, 3], ns[5:7]),
            ("a message from a child session does not reset them", ns[7:9] == [3, 3], ns[7:9]),
            ("a message whose text starts with the plugin marker line, sent as if typed, does not reset them",
             ns[9:11] == [3, 3], ns[9:11]),
            ("control: a genuinely typed message does reset them (the next idle re-prompts)", ns[11:13] == [3, 4], ns[11:13])]


def case_t08():
    print("[CP6-T08] echoes, sub-agent messages and marker-shaped text do not reset the caps")
    w = fresh()
    before = log_entries(w)
    o = drive(w, [run_of(w.plugin, t08_steps())])
    emit("CP6-T08", v_t08(o))
    after = log_entries(w)
    new = [e.get("prompt") for e in after[len(before):]]
    check("[CP6-T08] the engine's stop-block count is not reset by them: only the control message is a new human "
          "reply in human_log.jsonl", new == ["real words from the user"], new)
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that lets its own echo reset the caps", {"filt_token": False, "filt_prefix": False}, False),
                               ("a plugin that lets a child session's message reset the caps (it forgets every session's count)",
                                {"filt_child": False, "reset_all": True}, False),
                               ("a plugin that lets marker-shaped text reset the caps", {"filt_prefix": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), t08_steps())])
        tooth("CP6-T08", name, v_t08(o2), expect)


# ---- T09

def t09_steps():
    return [{"op": "setPrompt", "set": {"mode": "pending"}},
            idle(hold=True), idle(hold=True), idle(hold=True),               # 1: starts one; 2, 3: ignored
            {"op": "release"},                                               # 4
            idle(hold=True),                                                 # 5: the second
            {"op": "release"}, {"op": "setPrompt", "set": {"mode": "resolve"}},
            idle(), idle()]                                                  # 8: the third; 9: the cap


def v_t09(o):
    ns, st = o.ns(), o.statuses()
    return [("setup: the plugin loads and nothing crashed", o.loaded() and o.clean(), o.problems),
            ("the first idle starts a re-prompt that stays pending (one prompt call exists)",
             st[1] in ("held", "resolved") and ns[1] == 1, (st[1], ns[1])),
            ("two more idles for the same session while it is pending make no second call (still 1)",
             ns[2:4] == [1, 1] and st[2:4] == ["resolved", "resolved"], (ns[2:4], st[2:4])),
            ("after it resolves, the next idle sends the second (total 2)", ns[5] == 2 and ns[6] == 2, (ns[5], ns[6])),
            ("the ignored idles count as nothing: the third is still allowed, then the cap holds (3, 3)",
             ns[8:10] == [3, 3], ns[8:10])]


def case_t09():
    print("[CP6-T09] one re-prompt at a time per session")
    w = fresh()
    o = drive(w, [run_of(w.plugin, t09_steps())])
    emit("CP6-T09", v_t09(o))
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that allows two re-prompts in flight", {"inflight": False}, False),
                               ("a plugin that counts the ignored idles toward the cap", {"count_ignored": True}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), t09_steps())])
        tooth("CP6-T09", name, v_t09(o2), expect)


# ---- T10

def t10_a():
    return [idle(), {"op": "timers"}, {"op": "fireTimers"}] + [idle() for _ in range(10)]


def v_t10(o, label, mode_note=""):
    ns, st = o.ns(), o.statuses()
    return [(f"{label}: the plugin loads and nothing crashed or rejected unhandled", o.loaded() and o.clean(), o.problems),
            (f"{label}: after the first idle there is exactly 1 prompt call (no inline retry)", ns[0] == 1, ns[0]),
            (f"{label}: firing every timer the handler left behind makes no further call (no timer-driven retry)",
             ns[2] == 1, (ns[1:3], o.step(0, 1).get("pending"))),
            (f"{label}: every one of the eleven idle handlers resolves, none throws", st == ["resolved"] * 13, st),
            (f"{label}: failed attempts count toward the in-a-row cap: eleven idles make exactly 3 calls", ns[-1] == 3, ns[-1])]


def t10_recover():
    return [idle(), {"op": "setPrompt", "set": {"mode": "resolve"}}, idle(), idle(), idle()]


def v_t10_recover(o):
    ns = o.ns()
    return [("failure then success: the in-flight flag is cleared after the failure (the next idle sends, 2 calls), "
             "and the failed attempt counted toward the cap (3 after the third idle, still 3 after the fourth)",
             o.loaded() and o.clean() and ns == [1, 1, 2, 3, 3], ns)]


def case_t10():
    print("[CP6-T10] a failing prompt call is swallowed and never retried in a loop")
    w = stub_world()
    p, _stub, n = stubbed(w, {"kind": "const", "reason": STUB_REASON}, "t10")
    check("[CP6-T10] setup: the rendered plugin copy names the engine as a JSON string literal", n >= 1, n)
    for label, mode in (("prompt rejects", "reject"), ("prompt throws synchronously", "throw")):
        o = drive(w, [run_of(p, t10_a(), prompt={"mode": mode, "echo": False})])
        emit("CP6-T10", v_t10(o, label))
    o = drive(w, [run_of(p, t10_recover(), prompt={"mode": "reject", "echo": False})])
    emit("CP6-T10", v_t10_recover(o))
    rw = fresh()
    o = drive(rw, [run_of(rw.plugin, t10_a(), prompt={"mode": "reject", "echo": False})])
    emit("CP6-T10", v_t10(o, "real engine, prompt rejects"))
    stub = const_stub(w)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that retries inline", {"retry_inline": True}, False),
                               ("a plugin that retries from a timer", {"retry_timer": True}, False),
                               ("a plugin that throws the failure into OpenCode", {"throw_out": True}, False)):
        o2 = drive(w, [run_of(ref_plugin(w, stub, **opts), t10_a(), prompt={"mode": "reject", "echo": False})])
        tooth("CP6-T10", name, v_t10(o2, name), expect)
    for name, opts, expect in (("reference plugin (recovery)", {}, True),
                               ("a plugin that never clears its in-flight flag after a failure", {"clear_inflight": False}, False)):
        o2 = drive(w, [run_of(ref_plugin(w, stub, **opts), t10_recover(), prompt={"mode": "reject", "echo": False})])
        tooth("CP6-T10", name, v_t10_recover(o2), expect)


# ---- T11

T11_TABLE = {**TABLE, "ses_unknown": {"$reject": True}, "ses_thrower": {"$throw": True},
             "ses_err": {"error": {"name": "NotFoundError"}}, "ses_nodata": {"data": None}}


def t11_steps():
    return [idle("ses_child"), idle("ses_unknown"), idle("ses_thrower"), idle("ses_err"), idle("ses_nodata"), idle("ses_main")]


def v_t11(o):
    asked = [(s[0] or {}).get("session_id") for s in o.engine_calls("stop")]
    return [("setup: the plugin loads, every handler resolves (a failed lookup never throws out), nothing crashed",
             o.loaded() and o.clean() and all_resolved(o), (o.statuses(), o.problems)),
            ("a child session (parentID set) and four failed lookups (reject, sync throw, error answer, no data) are "
             "never re-prompted", o.ns()[:5] == [0, 0, 0, 0, 0], o.ns()),
            ("the skip is per session: ses_main, in the same run, is re-prompted", o.ns()[5] == 1 and o.sids() == ["ses_main"],
             (o.ns()[5], o.sids())),
            ("a skipped session is skipped before the engine is asked (its stop count is shared by the run): only "
             "ses_main reached `hook stop`", asked == ["ses_main"], asked)]


def case_t11():
    print("[CP6-T11] a sub-agent session is never re-prompted")
    w = fresh()
    o = drive(w, [run_of(w.plugin, t11_steps(), table=T11_TABLE)])
    emit("CP6-T11", v_t11(o))
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that re-prompts child sessions", {"skip_child": False}, False),
                               ("a plugin that re-prompts when the session lookup fails", {"lookup_fail_open": True}, False),
                               ("a plugin that asks the engine before checking the session", {"engine_first": True}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), t11_steps(), table=T11_TABLE)])
        tooth("CP6-T11", name, v_t11(o2), expect)


# ---- T12

def t12_steps():
    return [abort_ev("ses_main"), idle("ses_main"),                            # 0, 1: cancelled, then idle
            seam_step("echo of a plugin turn", "mk"),                          # 2
            chat([part(var("mk"), mid="msg_echo_a")], mid="msg_echo_a"),       # 3: an echo is not the user typing
            idle("ses_main"),                                                  # 4
            idle("ses_other"),                                                 # 5: never cancelled
            typed("sorry, go on"),                                             # 6: the user's own message
            idle("ses_main")]                                                  # 7


def v_t12(o):
    ns = o.ns()
    return [("setup: the plugin loads, every step resolves, nothing crashed", o.loaded() and o.clean() and all_resolved(o),
             (o.statuses(), o.problems)),
            ("no re-prompt right after the cancel (the session.error MessageAbortedError came before the idle)",
             ns[:2] == [0, 0], ns[:2]),
            ("a re-prompt echo does not clear the cancel (the next idle still sends nothing)", ns[3:5] == [0, 0], ns[3:5]),
            ("a different session that was never cancelled is re-prompted normally (the cancel is per session)",
             ns[5] == 1 and o.sids()[:1] == ["ses_other"], (ns[5], o.sids())),
            ("after the user's own new message the next idle re-prompts ses_main again",
             ns[6:8] == [1, 2] and o.sids() == ["ses_other", "ses_main"], (ns[6:8], o.sids()))]


def case_t12():
    print("[CP6-T12] a session the user cancelled is never re-prompted")
    w = fresh()
    o = drive(w, [run_of(w.plugin, t12_steps())])
    emit("CP6-T12", v_t12(o))
    asked = [(s[0] or {}).get("session_id") for s in o.engine_calls("stop")]
    check("[CP6-T12] a cancelled session is skipped before the engine is asked (only ses_other and the final ses_main "
          "idle reached `hook stop`)", asked == ["ses_other", "ses_main"], asked)
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that ignores the cancel signal", {"skip_abort": False}, False),
                               ("a plugin where one cancel silences every session", {"abort_global": True}, False),
                               ("a plugin where any chat message clears the cancel", {"abort_clear_any": True}, False),
                               ("a plugin that never clears the cancel", {"abort_clear": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), t12_steps())])
        tooth("CP6-T12", name, v_t12(o2), expect)


# ---- T13

def t13_malformed_steps():
    js = ["undefined", "null", "({})", "({event: undefined})", "({event: null})", "({event: {}})",
          "({event: 'session.idle'})", "({event: {type: 'session.idle'}})",
          "({event: {type: 'session.idle', properties: null}})", "({event: {type: 'session.idle', properties: {}}})",
          "({event: {type: 'session.idle', properties: {sessionID: 123}}})",
          "({event: {type: 'session.idle', properties: {sessionID: ''}}})",
          "({event: {type: 'session.idle', properties: {sessionID: null}}})",
          "({event: {type: 'session.idle', properties: {sessionID: {}}}})",
          "({event: {type: 7, properties: {sessionID: 'ses_main'}}})", "'session.idle'", "42", "[]"]
    return [raw_event(j) for j in js]


def t13_reply_variants():
    return [("block true with no message", {"block": True}), ("block true with an empty message", {"block": True, "message": ""}),
            ("block true with a number as message", {"block": True, "message": 123}),
            ("block true with null as message", {"block": True, "message": None}),
            ("block true with an object as message", {"block": True, "message": {"a": 1}}),
            ("block as the string 'true'", {"block": "true", "message": "do the thing"}),
            ("block as the number 1", {"block": 1, "message": "do the thing"})]


def v_t13_malformed(o, label):
    n = len(o.statuses())
    return [(f"{label}: setup: the plugin loads and the node process stays alive with no unhandled rejection",
             o.loaded() and o.clean(), (o.run().get("factory"), o.problems)),
            (f"{label}: the event handler's promise resolves for every one of the {n} malformed events (never rejects or throws)",
             n == 18 and all_resolved(o), [s for s in o.statuses() if s != "resolved"]),
            (f"{label}: no re-prompt is sent for a malformed event", o.final_n() == 0, o.final_n())]


def v_t13_variant(o, label, expect_n):
    return [(f"{label}: the plugin loads, the handler resolves (never rejects), no unhandled rejection",
             o.loaded() and o.clean() and o.statuses() == ["resolved"], (o.statuses(), o.problems)),
            (f"{label}: {expect_n} re-prompt(s) sent", o.ns() == [expect_n], o.ns())]


def case_t13():
    print("[CP6-T13] the event hook can never throw into OpenCode")
    w = fresh()
    sw = stub_world()
    # (1) the engine path no longer exists; garbage on stdout; exit non-zero with a traceback
    missing = str(w.s.work / "no-such-folder" / "ratchet.py")
    variants = [("the engine path no longer exists", missing, 0),
                ("the engine prints garbage", stub_engine(sw, {"kind": "raw", "stdout": "this is not json {", "stderr": "", "code": 0}), 0),
                ("the engine exits non-zero with a traceback", stub_engine(sw, {"kind": "raw", "stdout": "", "stderr": "Traceback (most recent call last): boom", "code": 1}), 0),
                ("the engine prints a JSON array", stub_engine(sw, {"kind": "raw", "stdout": "[1, 2]", "stderr": "", "code": 0}), 0)]
    for label, reply in t13_reply_variants():
        variants.append((f"the engine answers {label}", stub_engine(sw, {"kind": "reply", "reply": reply}), 0))
    variants.append(("control: a well-formed block reply", stub_engine(sw, {"kind": "reply", "reply": {"block": True, "message": "do the next step"}}), 1))
    runs, copies = [], []
    for i, (_l, eng, _n) in enumerate(variants):
        cp, hits = retargeted_plugin(sw, f"t13-{i}", eng)
        copies.append(hits)
        runs.append(run_of(cp, [idle()]))
    # session lookup throws synchronously; prompt throws synchronously (a well-formed block still attempts once)
    cp, hits = retargeted_plugin(sw, "t13-get", stub_engine(sw, {"kind": "const", "reason": STUB_REASON}))
    copies.append(hits)
    runs.append(run_of(cp, [idle()], table={"ses_main": {"$throw": True}}))
    cp, hits = retargeted_plugin(sw, "t13-prompt", stub_engine(sw, {"kind": "const", "reason": STUB_REASON}))
    copies.append(hits)
    runs.append(run_of(cp, [idle()], prompt={"mode": "throw", "echo": False}))
    cp, hits = retargeted_plugin(sw, "t13-ev", stub_engine(sw, {"kind": "const", "reason": STUB_REASON}))
    copies.append(hits)
    runs.append(run_of(cp, t13_malformed_steps()))
    o = drive(sw, runs)
    check("[CP6-T13] setup: every plugin copy names the engine as a JSON string literal", all(n >= 1 for n in copies), copies)
    check("[CP6-T13] setup: the node process was clean over all variants (no unhandled rejection, no crash)", o.clean(), o.problems)
    for i, (label, _eng, n) in enumerate(variants):
        emit("CP6-T13", [(l, c, d) for l, c, d in v_t13_variant(o.view(i), label, n)])
    k = len(variants)
    emit("CP6-T13", v_t13_variant(o.view(k), "session.get throws synchronously", 0))
    emit("CP6-T13", v_t13_variant(o.view(k + 1), "session.prompt throws synchronously", 1))
    emit("CP6-T13", v_t13_malformed(o.view(k + 2), "malformed events (undefined, null, no properties, bad sessionID, ...)"))
    # teeth
    stub_ok = const_stub(sw)
    ev_steps = t13_malformed_steps()
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin with no guard around malformed events", {"guard_event": False, "throw_out": True}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub_ok, **opts), ev_steps)])
        tooth("CP6-T13", name, v_t13_malformed(o2, name), expect)
    for name, opts, expect in (("reference plugin (empty message)", {}, True),
                               ("a plugin that re-prompts on an empty message", {"strict_msg": False}, False)):
        st = stub_engine(sw, {"kind": "reply", "reply": {"block": True, "message": ""}})
        o2 = drive(sw, [run_of(ref_plugin(sw, st, **opts), [idle()])])
        tooth("CP6-T13", name, v_t13_variant(o2, name, 0), expect)
    for name, opts, expect in (("reference plugin (block 'true')", {}, True),
                               ("a plugin that reads any truthy block as a block", {"loose_flag": True}, False)):
        st = stub_engine(sw, {"kind": "reply", "reply": {"block": "true", "message": "do the thing"}})
        o2 = drive(sw, [run_of(ref_plugin(sw, st, **opts), [idle()])])
        tooth("CP6-T13", name, v_t13_variant(o2, name, 0), expect)


# ---- T14

def t14_steps():
    sid = {"sessionID": "ses_main"}
    return [ev("session.status", {**sid, "status": {"type": "busy"}}), ev("session.status", {**sid, "status": {"type": "idle"}}),
            ev("message.updated", {**sid, "info": {"id": "msg_1", "role": "assistant"}}),
            ev("message.part.updated", {**sid, "part": {"id": "prt_1", "type": "text", "text": "hi"}}),
            ev("session.created", {**sid, "info": {"id": "ses_main"}}), ev("session.updated", {**sid, "info": {"id": "ses_main"}}),
            ev("session.error", {**sid, "error": {"name": "UnknownError", "data": {"message": "boom"}}}),
            ev("session.compacted", sid), ev("tool.execute.after", {**sid, "tool": "bash"}),
            ev("permission.asked", {**sid, "id": "per_1"}), ev("file.edited", {"file": "a.js"}),
            ev("server.connected", {}), ev("totally.unknown.event", {**sid, "x": 1}), ev("session.idle.extra", sid)]


def v_t14(o, label="open gate"):
    return [(f"{label}: the plugin loads and every handler resolves (no throw), nothing crashed",
             o.loaded() and o.clean() and all_resolved(o) and len(o.statuses()) == 14, (o.statuses(), o.problems)),
            (f"{label}: no prompt call for any of the other event kinds", o.final_n() == 0, o.final_n()),
            (f"{label}: no engine call at all (nothing asked, so nothing counted against the run's stop valve)",
             o.engine_calls("stop") == [] and o.spy == [], [s.get("argv") for s in o.spy])]


def case_t14():
    print("[CP6-T14] only session.idle and the cancel signal matter")
    w = fresh()
    o = drive(w, [run_of(w.plugin, t14_steps())])
    emit("CP6-T14", v_t14(o))
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that also reacts to session.status idle (a double fire)", {"react_status_idle": True}, False),
                               ("a plugin that reacts to every event", {"react_any": True}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), t14_steps())])
        tooth("CP6-T14", name, v_t14(o2, name), expect)


# ---- T15

def t15_steps():
    return [{"op": "setPrompt", "set": {"mode": "pending"}},
            idle("ses_a", hold=True),                                    # 1: a's first (pending)
            idle("ses_b", hold=True),                                    # 2: b's first, a pending does not block b
            idle("ses_a", hold=True),                                    # 3: a again: blocked by its own pending
            idle("ses_b", hold=True),                                    # 4: b again: blocked by its own pending
            {"op": "release"}, {"op": "setPrompt", "set": {"mode": "resolve"}},
            idle("ses_a"), idle("ses_a"), idle("ses_a"),                 # 7, 8, 9: a to its 3, then nothing
            idle("ses_b"), idle("ses_b"), idle("ses_b")]                 # 10, 11, 12: b to its own 3, then nothing


def v_t15(o):
    ns = o.ns()
    sids = o.sids()
    return [("setup: the plugin loads and nothing crashed", o.loaded() and o.clean(), o.problems),
            ("ses_b gets its own re-prompt while ses_a's is still pending (a pending one blocks only its own session)",
             ns[1] == 1 and ns[2] == 2 and sids[:2] == ["ses_a", "ses_b"], (ns[1:3], sids[:2])),
            ("a pending re-prompt blocks only its own session's next idle (both stay at 2 prompts)", ns[3] == 2 and ns[4] == 2,
             ns[3:5]),
            ("ses_a gets exactly 3 in a row and nothing more (3, 4, 4 after its three idles)", ns[7:10] == [3, 4, 4], ns[7:10]),
            ("one session's count never uses up the other's: ses_b also gets its own 3 (5, 6, 6), 6 prompts in all",
             ns[10:13] == [5, 6, 6] and sids == ["ses_a", "ses_b", "ses_a", "ses_a", "ses_b", "ses_b"], (ns[10:13], sids))]


def case_t15():
    print("[CP6-T15] caps and in-flight state are per session")
    w = fresh()
    o = drive(w, [run_of(w.plugin, t15_steps())])
    emit("CP6-T15", v_t15(o))
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin with one cap shared by every session", {"per_session": False}, False),
                               ("a plugin with one in-flight flag shared by every session", {"inflight_per_session": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), t15_steps())])
        tooth("CP6-T15", name, v_t15(o2), expect)


# ---- T16

def t16_steps():
    return [typed("please continue the plan", sid="ses_owner"), idle("ses_visitor"), idle("ses_owner")]


def v_t16(o):
    asked = [s[0] or {} for s in o.engine_calls("stop")]
    return [("setup: the plugin loads, every step resolves, nothing crashed", o.loaded() and o.clean() and all_resolved(o),
             (o.statuses(), o.problems)),
            ("ses_visitor gets no re-prompt (the engine was asked about it and said it is not bound)",
             o.ns()[1] == 0 and "ses_visitor" in [a.get("session_id") for a in asked], (o.ns(), asked)),
            ("ses_owner gets one", o.ns()[2] == 1 and o.sids() == ["ses_owner"], (o.ns(), o.sids())),
            ("the plugin passed each idle's own session id to `hook stop --host opencode`",
             [a.get("session_id") for a in asked] == ["ses_visitor", "ses_owner"], [a.get("session_id") for a in asked])]


def case_t16():
    print("[CP6-T16] a run owned by another OpenCode session is not re-prompted from this one")
    w = fresh()
    o = drive(w, [run_of(w.plugin, t16_steps())])
    res = v_t16(o)
    asked = [s[0] or {} for s in o.engine_calls("stop")]
    res.append(("the plugin passed the project folder as cwd", asked and all(a.get("cwd") == str(w.proj) for a in asked),
                [a.get("cwd") for a in asked]))
    res.append(("setup: the owner was set the normal way, by the typed message in ses_owner coming through the plugin "
                "(human_log.jsonl has it, with its session)",
                any(e.get("session") == "ses_owner" and e.get("prompt") == "please continue the plan" for e in log_entries(w)),
                [(e.get("session"), e.get("prompt")) for e in log_entries(w)]))
    emit("CP6-T16", res)
    sw = stub_world()
    stub = stub_engine(sw, {"kind": "owner", "owner": "ses_owner", "reason": STUB_REASON})
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that does not tell the engine which session is idle", {"session_in_payload": False}, False)):
        o2 = drive(sw, [run_of(ref_plugin(sw, stub, **opts), t16_steps())])
        tooth("CP6-T16", name, v_t16(o2), expect)


# ---- T17

def stop_errors_count(w):
    p = w.s.rh / "stop-errors.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    vals = [v for v in data.values() if isinstance(v, int)] if isinstance(data, dict) else []
    return max(vals) if vals else 0


def damaged_world():
    w = fresh()
    (w.run_dir / "state.json").write_text('{"phase": "build', encoding="utf-8")
    return w


def v_t17(o, w):
    replies = [s[1] for s in o.engine_calls("stop")]
    texts = [p["text"] or "" for p in o.sent()]
    gave_up = [r for r in replies if isinstance(r, dict) and "letting you stop after repeated failures" in str(r.get("message"))]
    cnt = stop_errors_count(w)
    return [("setup: the plugin loads, all six idle handlers resolve, nothing crashed",
             o.loaded() and o.clean() and o.statuses() == ["resolved"] * 6, (o.statuses(), o.problems)),
            ("the engine's fail-closed reason is delivered in a re-prompt (a well-formed block is honored)",
             len(texts) >= 1 and all("could not read its run state" in t for t in texts), [t[:120] for t in texts]),
            ("at most 3 are sent in a row (exactly 3 over six idles)", o.ns() == [1, 2, 3, 3, 3, 3], o.ns()),
            ("the engine's own count of failed stops never passed its limit of five (it never said 'letting you stop "
             "after repeated failures'): the plugin stopped first", len(replies) >= 3 and not gave_up and (cnt is not None and 3 <= cnt <= 5), (cnt, len(replies)))]


def case_t17():
    print("[CP6-T17] a broken run state is fail-closed in the engine but still capped in the plugin")
    w = damaged_world()
    direct = engine_stop(damaged_world())
    check("[CP6-T17] setup: with the damaged state the engine's stop answer is its fail-closed block ('could not read "
          "its run state')", direct.get("block") is True and "could not read its run state" in str(direct.get("message")),
          direct)
    o = drive(w, [run_of(w.plugin, [idle() for _ in range(6)], prompt={"mode": "resolve", "echo": True})])
    emit("CP6-T17", v_t17(o, w))
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin with no caps (it would run into the engine's own count)", {"cap_row": False, "ceiling": False}, False)):
        w2 = damaged_world()
        o2 = drive(w2, [run_of(ref_plugin(w2, ENGINE, **opts), [idle() for _ in range(6)], prompt={"mode": "resolve", "echo": True})])
        tooth("CP6-T17", name, v_t17(o2, w2), expect)


# ---- T18

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
    with _tmp.mkdtemp(prefix="ratchet-ocstop-probe-") as cwd:
        r = subprocess.run([sys.executable, "-c", code, str(ENGINE)], cwd=cwd, capture_output=True, text=True, env=env,
                           timeout=180)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def t18_steps():
    return [typed("hello there"), guard_step("bash", {"command": "ls"}), seam_step("a reason", "mk"),
            chat([part(var("mk"), mid="msg_e")], mid="msg_e")]


def v_t18_wiring(o_awaited, o_event, plugin_text):
    stops_a, stops_e = o_awaited.engine_calls("stop"), o_event.engine_calls("stop")
    return [("setup: the plugin has chat.message and tool.execute.before, and both ran in an open-gate project",
             o_awaited.loaded(hook="chat.message") and o_awaited.loaded(hook="tool.execute.before") and o_awaited.clean()
             and o_awaited.statuses() == ["resolved"] * 4, (o_awaited.run().get("hookNames"), o_awaited.statuses(), o_awaited.problems)),
            ("the rendered plugin carries the stop check ('hook stop --host opencode')",
             re.search(r"hook\W+stop\W+--host\W+opencode", plugin_text) is not None, "no stop command in the plugin"),
            ("the stop check is wired through the event hook: a session.idle event makes one `hook stop` call",
             o_event.loaded() and len(stops_e) == 1, (o_event.run().get("hookNames"), len(stops_e))),
            ("no awaited hook (chat.message, tool.execute.before) ever calls the stop check",
             stops_a == [], [s[0] for s in stops_a])]


def case_t18():
    print("[CP6-T18] the stop cell for OpenCode stays best-effort while the other four CLIs keep a real stop")
    enf = host_enforcement()
    check("[CP6-T18] setup: HOST_ENFORCEMENT was read by a child process", isinstance(enf, dict), enf)
    enf = enf if isinstance(enf, dict) else {}
    check("[CP6-T18] HOST_ENFORCEMENT['opencode']['stop'] is False (no hard stop on OpenCode)",
          (enf.get("opencode") or {}).get("stop") is False, enf.get("opencode"))
    check("[CP6-T18] claude, codex, gemini and copilot are still True", all((enf.get(h) or {}).get("stop") is True
                                                                          for h in ("claude", "codex", "gemini", "copilot")),
          {h: (enf.get(h) or {}).get("stop") for h in ("claude", "codex", "gemini", "copilot")})
    rows = md_table_rows(first_table(cli_section(README.read_text(encoding="utf-8"))))
    by_name = {r[0]: r for r in rows[1:] if r}
    oc = by_name.get("OpenCode", [])
    check("[CP6-T18] the README OpenCode row says exactly 'No, the stop hook cannot stop the agent from ending its turn "
          "early.' in the stop cell", len(oc) == 5 and oc[3] == STOP_CELL, oc[3] if len(oc) == 5 else list(by_name))
    check("[CP6-T18] the other four rows keep their 'Yes' stop cell",
          all(len(by_name.get(h, [])) == 5 and says_yes(by_name[h][3]) for h in ("Claude Code", "Codex", "Gemini", "Copilot")),
          {h: (by_name.get(h) or [None] * 4)[3] for h in ("Claude Code", "Codex", "Gemini", "Copilot")})
    w = fresh()
    cwd = str(w.proj)
    r = w.sh("hook", "stop", "--host", "claude", stdin=json.dumps({"session_id": "s", "cwd": cwd, "hook_event_name": "Stop", "stop_hook_active": False}))
    check("[CP6-T18] claude's stop hook still blocks with an open gate: exit 2, reason on stderr",
          r.returncode == 2 and "RATCHET GATE" in r.stderr, (r.returncode, r.stderr[:100]))
    r = w.sh("hook", "stop", "--host", "codex", stdin=json.dumps({"session_id": "s", "turn_id": "t1", "cwd": cwd, "hook_event_name": "Stop", "model": "gpt-5-codex", "stop_hook_active": False}))
    check("[CP6-T18] codex's stop hook still blocks: exit 2, reason on stderr", r.returncode == 2 and "RATCHET GATE" in r.stderr,
          (r.returncode, r.stderr[:100]))
    r = w.sh("hook", "stop", "--host", "gemini", stdin=json.dumps({"session_id": "s", "cwd": cwd, "hook_event_name": "AfterAgent", "prompt": "go", "prompt_response": "done!", "stop_hook_active": False, "timestamp": "2026-09-28T00:00:00Z"}))
    check("[CP6-T18] gemini's stop hook still blocks: exit 2, reason on stderr", r.returncode == 2 and "RATCHET GATE" in r.stderr,
          (r.returncode, r.stderr[:100]))
    r = w.sh("hook", "stop", "--host", "copilot", stdin=json.dumps({"sessionId": "s", "timestamp": 1790000000000, "cwd": cwd, "transcriptPath": "/tmp/t", "stopReason": "end_turn", "stop_hook_active": False}))
    try:
        out = json.loads(r.stdout or "{}")
    except ValueError:
        out = {}
    check("[CP6-T18] copilot's stop hook still blocks: exit 0 and a decision block", r.returncode == 0
          and out.get("decision") == "block" and "RATCHET GATE" in str(out.get("reason")), (r.returncode, r.stdout[:100]))
    ans = engine_stop(w)
    check("[CP6-T18] the engine's own opencode stop answer is a block ({block:true, message}) even though the cell says "
          "no hard stop", ans.get("block") is True and "RATCHET GATE" in str(ans.get("message")), ans)
    text = w.plugin.read_text(encoding="utf-8")
    o = drive(w, [run_of(w.plugin, t18_steps())])
    w_e = fresh()
    oe = drive(w_e, [run_of(w_e.plugin, [idle()])])
    emit("CP6-T18", v_t18_wiring(o, oe, text))
    sw = stub_world()
    stub = const_stub(sw)
    for name, opts, expect in (("reference plugin", {}, True),
                               ("a plugin that runs the stop check inside chat.message", {"stop_in_chat": True}, False)):
        pth = ref_plugin(sw, stub, **opts)
        o2 = drive(sw, [run_of(pth, t18_steps())])
        o3 = drive(sw, [run_of(pth, [idle()])])
        tooth("CP6-T18", name, v_t18_wiring(o2, o3, pth.read_text(encoding="utf-8")), expect)


# ---- T19

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


def case_t19():
    print("[CP6-T19] the new test file is listed in the README test list")
    text = README.read_text(encoding="utf-8")
    block = readme_test_block()
    lst = [ln for ln in block if ln.startswith("python3 tests/")]
    check("[CP6-T19] the 'Running the tests' list holds the exact line `python3 tests/test_hook_opencode_stop.py` exactly "
          "once, and it appears nowhere else", block.count(NEW_TEST_LINE) == 1 and text.splitlines().count(NEW_TEST_LINE) == 1,
          block.count(NEW_TEST_LINE))
    if NEW_TEST_LINE in lst:
        i = lst.index(NEW_TEST_LINE)
        ctx = lst[max(0, i - 1):i + 2]
        check("[CP6-T19] its previous neighbour in the list sorts before it", i == 0 or lst[i - 1] < NEW_TEST_LINE, ctx)
        check("[CP6-T19] its next neighbour in the list sorts after it", i + 1 >= len(lst) or lst[i + 1] > NEW_TEST_LINE, ctx)
        without = [x for x in lst if x != NEW_TEST_LINE]
        check("[CP6-T19] adding the line adds no out-of-order pair to the list", inversions(lst) == inversions(without),
              inversions(lst))
    else:
        for n in ("its previous neighbour in the list sorts before it", "its next neighbour in the list sorts after it",
                  "adding the line adds no out-of-order pair to the list"):
            check(f"[CP6-T19] {n}", False, "line missing")
    check("[CP6-T19] teeth: an out-of-order insertion adds an inversion, an in-order one does not",
          inversions(["a", "c", "b"]) != inversions(["a", "b"]) and inversions(["a", "b", "c"]) == inversions(["a", "c"]))


# ---- T20

def node_refusal_ok(rc, text):
    """A run without node: non-zero exit, a plain message that node is required, and no case reported as passed."""
    return (rc != 0 and re.search(r"\bnode\b[^\n]*\b(?:required|needed|not found|missing|install)", text, re.I) is not None
            and "✓ [CP6-" not in text and re.search(r"\d+ passed, 0 failed", text) is None
            and not re.search(r"\bskip", text, re.I))


def case_t20():
    print("[CP6-T20] a missing node fails loudly instead of passing")
    d = Path(_tmp.mkdtemp(prefix="ratchet-ocstop-nonode-")).resolve()
    shim = d / "path-without-node"
    shim.mkdir()
    for tool in ("git", "python3"):
        real = shutil.which(tool)
        if real and not (shim / tool).exists():
            os.symlink(real, shim / tool)
    work = d / "cwd"
    work.mkdir()
    env = scrubbed_env()
    env.update(PATH=str(shim), HOME=str(d), PYTHONDONTWRITEBYTECODE="1")
    check("[CP6-T20] setup: the PATH given to the script has no node", shutil.which("node", path=str(shim)) is None)
    t0 = time.time()
    p = subprocess.run([sys.executable, str(THIS_FILE)], env=env, capture_output=True, text=True, timeout=240, cwd=str(work))
    text = (p.stdout or "") + (p.stderr or "")
    check("[CP6-T20] it exits non-zero with a plain message that node is required, and reports no case as passed or "
          "skipped", node_refusal_ok(p.returncode, text), (p.returncode, text[:300]))
    check("[CP6-T20] it stops before touching any project: the child made no throwaway project and finished promptly",
          time.time() - t0 < 60 and "ratchet-ocstop" not in text, round(time.time() - t0, 1))
    check("[CP6-T20] teeth: exit 0, a message without node, a passed-case line and a clean summary are all rejected",
          not node_refusal_ok(0, "node is required")
          and not node_refusal_ok(2, "something failed")
          and not node_refusal_ok(2, "node is required\n  ✓ [CP6-T01] x")
          and not node_refusal_ok(1, "node is required\n3 passed, 0 failed")
          and node_refusal_ok(2, "ERROR: node is required to run this test"))


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
    for n in range(1, 21):
        guarded(globals()[f"case_t{n:02d}"], f"CP6-T{n:02d}")
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed ({time.time() - started:.0f} s)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
