#!/usr/bin/env python3
"""
OpenCode support, step 7: an offline smoke test of Ratchet's OpenCode plugin against the REAL installed OpenCode
(1.18.27) and the real `bun` runtime, with no model call and no credentials, that says plainly what it did and did not
exercise.

    python3 tests/test_hook_opencode_smoke.py      # needs opencode and bun for the full run; takes a few minutes

What it drives (three real things, all offline, all in throwaway folders):
  1. `opencode serve` (never --pure) started in its own process group against a temp project whose plugin was written
     by `ratchet.py install-hooks --host opencode --scope project`. OpenCode loads a project's plugins when the first
     directory-scoped request opens that project (GET /session?directory=...), so that request is made and the server's
     `--print-logs` output is polled up to a generous bound (that open took 20 to 45 s on a loaded machine; a timeout
     is a FAIL, never a pass and never a hang). Two projects are opened in the one server: a healthy one, and one
     whose plugin points at a Ratchet engine that was deleted after the install.
  2. `bun` importing the rendered plugin with a fake SDK client (counted session.prompt, recorded app.log) against the
     real ratchet.py engine, calling the guard (tool.execute.before), prompt (chat.message) and stop (session.idle)
     handlers the way OpenCode would. OpenCode itself does NOT fire these handlers in this test (that needs a model
     turn), so the report says that plainly. The same scenario table also runs under node and the outcomes are compared.
  3. `ratchet.py doctor` in the healthy project, in a project with no plugin, and in the missing-engine project.

Contract this file pins for the CP7 implementation (the plan leaves the wording open; these substrings are the stable
ones the checks look for, so the plugin and doctor must carry them):
  * at plugin init the plugin writes ONE line through `client.app.log` (any level) whose message contains the text
    "Ratchet plugin loaded" and the engine path it was installed with (case-sensitive "Ratchet plugin loaded");
  * when the engine path does not exist, the plugin ALSO writes, once at load (never once per tool call), one
    `client.app.log` line at level "warn" whose message contains, case-insensitively, the phrase "engine not found",
    the missing engine path, and the phrase "not running" (for example: "Ratchet engine not found at <path>: Ratchet's
    protections are not running. Re-run install-hooks --host opencode."), and it prints the same message with
    console.error. It does NOT throw (OpenCode must still load the plugin) and the guard still fails open;
  * with the engine present no log line that mentions Ratchet says "engine not found" or "not running" or "missing";
  * `ratchet.py doctor` in a project whose OpenCode plugin points at a missing engine keeps its `opencode` row with the
    plugin location and adds a line that contains the missing engine path, the word "missing" (case-insensitive) and
    the text "install-hooks --host opencode"; doctor exits 0. In a healthy install doctor prints no "missing" and no
    "broken" anywhere; with no plugin the opencode row says "not installed".

Test-only knobs (seconds; all optional):
  RATCHET_SMOKE_READY_TIMEOUT   how long the server may take to answer /global/health        (default 90)
  RATCHET_SMOKE_LOAD_TIMEOUT    how long the plugin load may take after the first request   (default 300)
  RATCHET_SMOKE_OVERALL_TIMEOUT hard limit for the whole script; past it every server is killed and it exits 3 (default 1500)

Safety. The server gets an environment built from nothing (PATH, a temp HOME, temp XDG config/data/state/cache folders,
a temp TMPDIR, three OPENCODE_DISABLE_* switches, and proxy variables pointing at a local recording listener the
test owns), never a copy of the developer's environment, so no *_API_KEY or token reaches it. The process's own HOME is
replaced at start by a decoy folder holding a sentinel plugin and an auth.json; the decoy is checksummed and must be
untouched at the end ([CP7-T04]). The server's whole process group is killed in a finally block, on SIGTERM, and by a
watchdog at the overall bound. The `opencode --version` calls get the same built-from-nothing environment. A missing
`opencode` or `bun` is reported as NOT RUN, counted apart from passed.

Re-runs of this same script in child processes (marked by RATCHET_SMOKE_CHILD) test its own behavior: a PATH without
opencode ([CP7-T15]), without bun ([CP7-T16]), a fake hanging opencode ([CP7-T17]) and the three ways a run can end
([CP7-T05]). Exit code: 1 when any check failed, 0 otherwise (a run where everything was NOT RUN exits 0 with the NOT RUN
banner on its own line).

Each case id appears in its check names as [CP7-Txx]. Browser cases do not exist in this checkpoint.
"""
import atexit
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
THIS_FILE = Path(__file__).resolve()
GROUP_OF = {"load": ["CP7-T01", "CP7-T02", "CP7-T03", "CP7-T04", "CP7-T11", "CP7-T12"], "lifecycle": ["CP7-T05"],
            "bun": ["CP7-T06", "CP7-T07", "CP7-T08", "CP7-T09", "CP7-T10"], "doctor": ["CP7-T13", "CP7-T14"],
            "meta": ["CP7-T15", "CP7-T16", "CP7-T17"], "report": ["CP7-T18"]}
CHILD = os.environ.get("RATCHET_SMOKE_CHILD", "")
GROUPS = set((os.environ.get("RATCHET_SMOKE_GROUPS") or ",".join(GROUP_OF)).split(","))
READY_TIMEOUT = float(os.environ.get("RATCHET_SMOKE_READY_TIMEOUT", "90"))
LOAD_TIMEOUT = float(os.environ.get("RATCHET_SMOKE_LOAD_TIMEOUT", "300"))
OVERALL_TIMEOUT = float(os.environ.get("RATCHET_SMOKE_OVERALL_TIMEOUT", "1500"))
PIN_VERSION = "1.18.27"
PREFIX = "[RATCHET PLUGIN TURN - NOT USER INPUT]"
LOADED = "Ratchet plugin loaded"
PASS, FAIL = 0, 0
RESULTS = {}     # case id -> [(ok, name, detail)]
NOT_RUN = {}     # case id -> reason
KEYS = {}        # report facts -> ok
STARTED = time.time()
_STRIP_PREFIXES = ("CODEX_", "OPENCODE_", "XDG_", "RATCHET_", "GEMINI_", "COPILOT_", "GITHUB_COPILOT_", "CLAUDE")
# Resolved BEFORE HOME is swapped for the decoy and before any PATH change.
OPENCODE = shutil.which("opencode")
BUN = shutil.which("bun")
NODE = shutil.which("node")
AMBIENT_PATH = os.environ.get("PATH", "")


def check(name, cond, detail="", key=None):
    global PASS, FAIL
    m = re.match(r"\[(CP7-[A-Z]\d+)\]", name)
    ok = bool(cond)
    if m:
        RESULTS.setdefault(m.group(1), []).append((ok, name, str(detail)[:300]))
    if key:
        KEYS[key] = ok and KEYS.get(key, True)
    if ok:
        PASS += 1
        print(f"  ✓ {name}", flush=True)
    else:
        FAIL += 1
        print(f"  ✗ {name}  {str(detail)[:600]}", flush=True)


def not_run(cid, reason):
    NOT_RUN[cid] = reason
    print(f"  NOT RUN: {reason} [{cid}]", flush=True)


def scrubbed_env():
    return {k: v for k, v in os.environ.items() if not k.startswith(_STRIP_PREFIXES)}


def git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), check=True, capture_output=True)


# ------------------------------------------------------------------------------------ processes and the clean-up

LIVE = []        # (Popen, pgid) of every server started here
CLEANUP_LOCK = threading.Lock()


def kill_group(pgid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + 3
        while time.time() < deadline and group_members(pgid):
            time.sleep(0.05)
        if not group_members(pgid):
            return


def kill_all():
    with CLEANUP_LOCK:
        for proc, pgid in list(LIVE):
            kill_group(pgid)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        LIVE.clear()


def group_members(pgid):
    """Pids of live (not zombie) processes in process group `pgid`."""
    out = []
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        try:
            raw = Path(f"/proc/{e}/stat").read_text()
        except OSError:
            continue
        rest = raw[raw.rfind(")") + 2:].split()
        if len(rest) > 2 and rest[0] != "Z" and int(rest[2]) == pgid:
            out.append(int(e))
    return out


def procs_mentioning(text):
    """Pids whose command line, working folder or environment mention `text` (a temp folder's path)."""
    me = os.getpid()
    needle = text.encode()
    out = []
    for e in os.listdir("/proc"):
        if not e.isdigit() or int(e) == me:
            continue
        try:
            if Path(f"/proc/{e}/stat").read_text().split(")")[-1].split()[0] == "Z":
                continue
        except OSError:
            continue
        for what in ("cmdline", "environ"):
            try:
                if needle in Path(f"/proc/{e}/{what}").read_bytes():
                    out.append(int(e))
                    break
            except OSError:
                pass
        else:
            try:
                if text in os.readlink(f"/proc/{e}/cwd"):
                    out.append(int(e))
            except OSError:
                pass
    return out


def proc_environ(pid):
    """The environment a live process really has (from /proc/<pid>/environ), or None when it cannot be read."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    return dict(kv.split("=", 1) for kv in raw.decode("utf-8", "replace").split("\0") if "=" in kv)


def environ_after_exec(pid, program, bound=5.0):
    """proc_environ(pid), read only once /proc shows the child has exec'd `program` and its environment is non-empty
    (right after a fork it is not yet the launch environment). Polls until `bound` seconds; then returns what it has."""
    deadline = time.monotonic() + bound
    env = None
    while time.monotonic() < deadline:
        try:
            execd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].endswith(program.encode())
        except OSError:
            execd = False
        env = proc_environ(pid)
        if execd and env:
            return env
        time.sleep(0.02)
    return env


def port_refuses(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def free_port():
    for _ in range(50):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if port != 4096:
            return port
    raise RuntimeError("no free port")


def on_term(signum, _frame):
    raise SystemExit(128 + signum)


def watchdog():
    deadline = STARTED + OVERALL_TIMEOUT
    while time.time() < deadline:
        time.sleep(1)
    print(f"\nOVERALL TIME BOUND EXCEEDED ({OVERALL_TIMEOUT:.0f} s): killing every server and stopping. This run FAILED.",
          flush=True)
    kill_all()
    try:
        _tmp.cleanup_all()   # os._exit skips atexit: remove the temp roots and the decoy home here (best effort)
    except Exception:
        pass
    os._exit(3)


# --------------------------------------------------------------------------------------------- the traffic canary

class Canary:
    """A local listener used as HTTP(S)_PROXY: records the first line of every connection (a CONNECT host:port or an
    absolute-URI request line) and answers 502, so nothing leaves the machine and every attempt is counted."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        self.lines = []
        self.stop = False
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        self.sock.settimeout(0.5)
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self.one, args=(conn,), daemon=True).start()

    def one(self, conn):
        try:
            conn.settimeout(3)
            data = b""
            try:
                data = conn.recv(4096)
            except OSError:
                pass
            first = data.split(b"\r\n", 1)[0].decode("latin-1", "replace") if data else "(connected, sent nothing)"
            self.lines.append(first)
            try:
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
        finally:
            conn.close()

    def hosts(self):
        found = []
        for line in self.lines:
            m = re.match(r"CONNECT\s+(\S+)", line) or re.match(r"[A-Z]+\s+https?://([^/\s]+)", line)
            found.append(m.group(1) if m else line[:60])
        return found

    def close(self):
        self.stop = True
        try:
            self.sock.close()
        except OSError:
            pass


MODEL_PATH = re.compile(r"chat/completions|/v1/messages|/messages\b|/responses\b|/completions\b|generateContent", re.I)


def traffic_has_model_request(lines):
    return [x for x in lines if MODEL_PATH.search(x)]


# ------------------------------------------------------------------------------------------------ scratch and env

class Scratch:
    def __init__(self):
        self.root = Path(_tmp.mkdtemp(prefix="ratchet-ocsmoke-")).resolve()
        self.home = self.root / "home"
        self.rh = self.root / "rh"
        self.work = self.root / "work"
        self.tmp = self.root / "tmp"
        self.proj = self.root / "proj"
        for p in (self.home / ".config", self.home / ".local" / "share", self.home / ".local" / "state",
                  self.home / ".cache", self.rh, self.work, self.tmp, self.proj):
            p.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def nxt(self, stem):
        self.n += 1
        return f"{stem}-{self.n}"


SC = None


def sc():
    global SC
    if SC is None:
        SC = Scratch()
    return SC


def engine_env(**extra):
    s = sc()
    env = scrubbed_env()
    env.update(HOME=str(s.home), RATCHET_HOME=str(s.rh), XDG_CONFIG_HOME=str(s.home / ".config"),
               PYTHONDONTWRITEBYTECODE="1", PATH=os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]]))
    env.update(extra)
    return env


def engine(proj, *args, stdin=None, engine_path=None, timeout=180):
    return subprocess.run([sys.executable, str(engine_path or ENGINE), *args], cwd=str(proj), input=stdin,
                          capture_output=True, text=True, env=engine_env(), timeout=timeout)


def must(r):
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    return r


def server_env(root, canary_port, path):
    """The server's whole environment, built from nothing."""
    root = Path(root)
    home = root / "home"
    proxy = f"http://127.0.0.1:{canary_port}"
    return {
        "PATH": path, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"), "TMPDIR": str(root / "tmp"), "LANG": "C.UTF-8",
        "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "http_proxy": proxy, "https_proxy": proxy,
        "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
    }


def opencode_env():
    """The environment for the `opencode --version` calls: the server's own, built from nothing (no API key, no real proxy)."""
    return server_env(sc().root, Load.canary.port if Load.canary else 1, AMBIENT_PATH)


def env_isolated(env, root, canary_port):
    """Why this environment is NOT safe for the server, or '' when it is."""
    bad = []
    root = str(root)
    for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "TMPDIR"):
        if not str(env.get(k, "")).startswith(root + os.sep):
            bad.append(f"{k} is not under the temp folder")
    allowed = {"OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_DISABLE_MODELS_FETCH", "OPENCODE_DISABLE_DEFAULT_PLUGINS"}
    for k, v in env.items():
        if re.search(r"API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", k, re.I):
            bad.append(f"{k} carries a credential name")
        if k.startswith(("OPENCODE_",)) and k not in allowed:
            bad.append(f"{k} is an unexpected OpenCode variable")
        if k.startswith(("CODEX_", "CLAUDE", "GEMINI_", "COPILOT_", "GITHUB_", "RATCHET_")):
            bad.append(f"{k} is an ambient CLI variable")
        if "PROXY" in k.upper() and k.upper() not in ("NO_PROXY",) and f":{canary_port}" not in v:
            bad.append(f"{k} does not point at the canary")
    return "; ".join(bad)


# ------------------------------------------------------------------------------------------ the throwaway projects

def _checkpoints(run):
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))


TEMPLATES = {}


def template(kind):
    """'building': an approved run in its building phase with an open gate. 'waiting': a plan-submitted run with the
    approval question asked (a typed reply approves the plan). 'bare': a git project with no .ratchet folder and no
    plugin. The plugin (written by the public install command, against the real engine) is in 'building', 'waiting'."""
    if kind in TEMPLATES:
        return TEMPLATES[kind]
    s = sc()
    base = s.proj / f"t-{kind}"
    base.mkdir()
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(base, *c)
    (base / "a.js").write_text("x")
    (base / "src").mkdir()
    (base / "src" / "app.js").write_text("a")
    git(base, "add", "-A")
    git(base, "commit", "-qm", "init")
    if kind != "bare":
        must(engine(base, "init", "opencode smoke", "--slug", "hk"))
        _checkpoints(base / ".ratchet" / "runs" / "hk")
        must(engine(base, "plan-submit"))
        must(engine(base, "install-hooks", "--host", "opencode", "--scope", "project"))
        must(engine(base, "ask", "approve-plan"))
        if kind == "building":
            import datetime as dt
            tp = s.work / f"{kind}-transcript.jsonl"
            tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "approved"},
                                      "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
            must(engine(base, "hook", "prompt", "--host", "claude",
                        stdin=json.dumps({"cwd": str(base), "prompt": "approved", "transcript_path": str(tp)})))
            must(engine(base, "approve-plan"))
    TEMPLATES[kind] = base
    return base


def fresh(kind, stem=None):
    src = template(kind)
    dst = sc().proj / sc().nxt(stem or f"p-{kind}")
    shutil.copytree(src, dst, symlinks=True)
    return dst


def plugin_of(proj):
    return Path(proj) / ".opencode" / "plugins" / "ratchet.js"


def skill_copy(kind):
    """A temp copy of the engine's scripts folder; the plugin is installed FROM it, so the plugin's engine path points
    at the copy. kind 'missing': ratchet.py is deleted afterwards. 'garbage': ratchet.py is replaced by a script that
    prints text that is not JSON. Returns (project, engine path of the copy)."""
    s = sc()
    stem = {"missing": "gone", "garbage": "junk"}[kind]   # (no "missing" in a folder name: the log checks look for that word)
    skill = s.root / s.nxt(f"skill-{stem}")
    scripts = skill / "scripts"
    shutil.copytree(ENGINE.parent, scripts, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ENGINE.parent.parent / "assets", skill / "assets")   # the plugin template lives here
    eng = scripts / "ratchet.py"
    proj = s.proj / s.nxt(f"p-{stem}")
    shutil.copytree(template("bare"), proj, symlinks=True)
    must(engine(proj, "install-hooks", "--host", "opencode", "--scope", "project", engine_path=eng))
    if kind == "missing":
        eng.unlink()
    elif kind == "garbage":
        eng.write_text("import sys\nsys.stdin.read()\nprint('this is not json {{{')\n")
    return proj, eng


def plugin_engine_path(proj):
    m = re.search(r'const RATCHET_ENGINE = ("(?:[^"\\]|\\.)*");', plugin_of(proj).read_text(encoding="utf-8"))
    return json.loads(m.group(1)) if m else None


# --------------------------------------------------------------------------------- the fake-client driver (bun, node)

DRIVER_JS = r"""
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

const [pluginPath, project, scenario] = process.argv.slice(2);
const logs = [], errs = [], prompts = [];
console.error = (...a) => { errs.push(a.map(String).join(' ')); };
const table = { ses_child: { parentID: 'ses_top' } };
const client = {
  app: { log: async (arg) => {
    const b = arg && arg.body;
    logs.push(b ? { level: b.level, message: b.message, service: b.service } : { raw: true });
    return { data: true };
  } },
  session: {
    get: async ({ path }) => ({ data: Object.assign({ id: path.id }, table[path.id] || {}) }),
    messages: async () => ({ data: [{ info: { role: 'assistant', finish: 'stop' } }] }),
    prompt: async (arg) => { prompts.push({ sid: arg.path.id, text: arg.body.parts[0].text }); return { data: {} }; },
  },
};
const out = { runtime: typeof Bun !== 'undefined' ? 'bun' : 'node', scenario };
const settle = async () => { for (let i = 0; i < 200 && logs.length === 0; i++) await new Promise((r) => setTimeout(r, 5)); };
const abs = (p) => project + '/' + p;
try {
  const mod = await import(pathToFileURL(pluginPath).href);
  const hooks = await mod.RatchetPlugin({ client, directory: project, worktree: project });
  await settle();
  out.hookNames = Object.keys(hooks).sort();
  out.logsAfterInit = logs.slice();
  out.errsAfterInit = errs.slice();
  if (scenario === 'guard') {
    const calls = [
      ['write', { filePath: abs('.ratchet/runs/hk/state.json'), content: '{}' }],
      ['edit', { filePath: abs('src/app.js'), oldString: 'a', newString: 'b' }],
      ['bash', { command: 'ls' }],
    ];
    out.guard = [];
    for (let i = 0; i < calls.length; i++) {
      const r = { tool: calls[i][0], threw: false, message: null };
      try { await hooks['tool.execute.before']({ tool: calls[i][0], sessionID: 'ses_top', callID: 'call_' + i }, { args: calls[i][1] }); }
      catch (e) { r.threw = true; r.message = String(e && e.message); r.isError = e instanceof Error; }
      out.guard.push(r);
    }
  } else if (scenario === 'prompt' || scenario === 'prompt_untyped') {
    const marker = hooks.ratchetMarkedText('Ratchet says: carry on');
    const msgs = scenario === 'prompt'
      ? [['typed', 'ses_top', 'msg_a', 'approved'], ['marker', 'ses_top', 'msg_b', marker], ['child', 'ses_child', 'msg_c', 'approved too']]
      : [['marker', 'ses_top', 'msg_b', marker], ['child', 'ses_child', 'msg_c', 'approved']];
    out.prompt = [];
    for (const [name, sid, mid, text] of msgs) {
      const output = { message: { id: mid }, parts: [{ id: 'prt_0', sessionID: sid, messageID: mid, type: 'text', text }] };
      let threw = false;
      try { await hooks['chat.message']({ sessionID: sid, messageID: mid }, output); } catch (e) { threw = true; }
      const extra = output.parts.slice(1);
      out.prompt.push({ name, threw, parts: output.parts.length, guidance: extra.map((p) => ({ type: p.type, synthetic: p.synthetic === true, text: p.text })) });
    }
  } else if (scenario === 'stop') {
    const ev = (type, properties) => hooks.event({ event: { type, properties } });
    out.steps = [];
    const step = async (name, sid, fn) => { const before = prompts.length; await fn(); out.steps.push({ name, sid, calls: prompts.length - before }); };
    await step('top idle', 'ses_top', () => ev('session.idle', { sessionID: 'ses_top' }));
    await step('child idle', 'ses_child', () => ev('session.idle', { sessionID: 'ses_child' }));
    await step('aborted idle', 'ses_abort', async () => {
      await ev('session.error', { sessionID: 'ses_abort', error: { name: 'MessageAbortedError', data: { message: 'aborted' } } });
      await ev('session.idle', { sessionID: 'ses_abort' });
    });
  } else if (scenario === 'stop_one') {
    const before = prompts.length;
    await hooks.event({ event: { type: 'session.idle', properties: { sessionID: 'ses_top' } } });
    out.steps = [{ name: 'top idle', sid: 'ses_top', calls: prompts.length - before }];
  }
} catch (e) {
  out.crash = String((e && e.stack) || e);
}
out.logs = logs; out.errs = errs; out.prompts = prompts;
console.log('RESULT:' + JSON.stringify(out));
process.exit(0);
"""


def driver_file():
    p = sc().work / "driver.mjs"
    if not p.exists():
        p.write_text(DRIVER_JS, encoding="utf-8")
    return p


def drive(runtime_bin, plugin, proj, scenario, timeout=120):
    """One scenario of the fake-client driver under `runtime_bin`: the parsed RESULT object (or a dict with 'crash')."""
    try:
        r = subprocess.run([runtime_bin, str(driver_file()), str(plugin), str(proj), scenario], cwd=str(sc().work),
                           capture_output=True, text=True, env=engine_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"crash": f"driver timed out after {timeout} s"}
    for line in reversed((r.stdout or "").splitlines()):
        if line.startswith("RESULT:"):
            return json.loads(line[7:])
    return {"crash": f"no result (rc {r.returncode}): {(r.stdout or '')[-300:]} {(r.stderr or '')[-300:]}"}


def human_entries(proj):
    p = Path(proj) / ".ratchet" / "human_log.jsonl"
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


# --------------------------------------------------------------------------------------------- verdict predicates

def v_guard(res):
    g = res.get("guard") or []
    first = g[0] if len(g) > 0 else {}
    msg = first.get("message") or ""
    return [
        ("the driver ran without a crash and the plugin has a tool.execute.before hook",
         "crash" not in res and "tool.execute.before" in (res.get("hookNames") or []), res.get("crash") or res.get("hookNames")),
        ("a write to .ratchet/runs/<slug>/state.json throws an Error with Ratchet's reason, which names state.json",
         first.get("threw") is True and first.get("isError") is True and msg.strip() != "" and "state.json" in msg,
         first),
        ("an edit of src/app.js resolves without throwing", len(g) > 1 and g[1].get("threw") is False, g[1:2]),
        ("the command `ls` resolves without throwing", len(g) > 2 and g[2].get("threw") is False, g[2:3]),
    ]


def v_prompt(res, entries):
    p = {x["name"]: x for x in (res.get("prompt") or [])}
    typed = p.get("typed", {})
    only = [e for e in entries if isinstance(e, dict)]
    return [
        ("the driver ran without a crash and every chat.message call resolved",
         "crash" not in res and len(p) == 3 and not any(x.get("threw") for x in p.values()), res.get("crash") or list(p)),
        ("exactly one reply is recorded: the typed text 'approved' from session ses_top",
         len(only) == 1 and only[0].get("prompt") == "approved" and only[0].get("session") == "ses_top", only),
        ("Ratchet's guidance for the turn is appended to the typed message's output.parts (one synthetic text part)",
         typed.get("parts") == 2 and len(typed.get("guidance") or []) == 1 and typed["guidance"][0].get("type") == "text"
         and typed["guidance"][0].get("synthetic") is True and (typed["guidance"][0].get("text") or "").strip() != "", typed),
        ("the plugin's own marker message gets nothing appended", p.get("marker", {}).get("parts") == 1, p.get("marker")),
        ("the child (sub-agent) session's message gets nothing appended", p.get("child", {}).get("parts") == 1, p.get("child")),
    ]


def v_prompt_untyped(res, entries, approve_rc):
    return [
        ("the driver ran without a crash", "crash" not in res and len(res.get("prompt") or []) == 2, res.get("crash")),
        ("neither the marker message nor the child-session message is recorded", entries == [], entries),
        ("and neither approves anything: `approve-plan` is still refused", approve_rc != 0, approve_rc),
    ]


def v_stop(res):
    steps = {x["name"]: x for x in (res.get("steps") or [])}
    sent = res.get("prompts") or []
    top = sent[0] if sent else {}
    text = top.get("text") or ""
    return [
        ("the driver ran without a crash", "crash" not in res and len(steps) == 3, res.get("crash") or list(steps)),
        ("client.session.prompt is called exactly once in total, for the top-level session",
         len(sent) == 1 and top.get("sid") == "ses_top" and steps.get("top idle", {}).get("calls") == 1, sent),
        ("the text carries the plugin's marker line and Ratchet's reason",
         text.startswith(PREFIX) and "RATCHET GATE" in text, text[:200]),
        ("an idle for a child session calls prompt zero times", steps.get("child idle", {}).get("calls") == 0, steps.get("child idle")),
        ("an idle for an aborted session calls prompt zero times", steps.get("aborted idle", {}).get("calls") == 0, steps.get("aborted idle")),
    ]


def v_stop_nogate(res):
    steps = res.get("steps") or []
    return [("a project with no open gate: the idle event calls prompt zero times",
             "crash" not in res and len(steps) == 1 and steps[0].get("calls") == 0 and (res.get("prompts") or []) == [],
             res.get("crash") or steps)]


def norm(obj, proj):
    """A scenario result with the project path and the per-load random token made comparable."""
    s = json.dumps(obj, sort_keys=True).replace(str(proj), "<PROJ>").replace(str(Path(proj).resolve()), "<PROJ>")
    s = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<UUID>", s)
    return s


def mutant_plugin(w_plugin, stem, old, new):
    """The rendered plugin with exactly one place changed (so each verdict is shown able to fail)."""
    src = Path(w_plugin).read_text(encoding="utf-8")
    if src.count(old) != 1:
        return None
    dst = Path(w_plugin).parent / f"{stem}.js"
    dst.write_text(src.replace(old, new), encoding="utf-8")
    return dst


def emit(cid, verdicts, key=None):
    for name, ok, detail in verdicts:
        check(f"[{cid}] {name}", ok, detail, key=key)


def teeth(cid, label, verdicts):
    """A mutant must be refused by at least one verdict of the case."""
    failed = [n for n, ok, _d in verdicts if not ok]
    check(f"[{cid}] teeth: {label} is caught by the same verdicts", failed != [], "the mutant passed every verdict")


# ------------------------------------------------------------------------------------------------- the real server

class Server:
    def __init__(self, root, canary, path=None):
        self.root = Path(root)
        self.port = free_port()
        self.out = self.root / "server.stdout.log"
        self.err = self.root / "server.stderr.log"
        self.path = path if path is not None else AMBIENT_PATH
        self.env = server_env(self.root, canary.port, self.path)
        self.proc = None
        self.pgid = None
        self.started_at = None
        self.threads = []
        self.open_errors = {}

    def start(self):
        exe = shutil.which("opencode", path=self.path)
        self.proc = subprocess.Popen([exe, "serve", "--port", str(self.port), "--print-logs", "--log-level", "DEBUG"],
                                     cwd=str(self.root), env=self.env, stdout=open(self.out, "wb"),
                                     stderr=open(self.err, "wb"), stdin=subprocess.DEVNULL, start_new_session=True)
        self.pgid = self.proc.pid
        self.started_at = time.time()
        with CLEANUP_LOCK:
            LIVE.append((self.proc, self.pgid))
        print(f"[smoke] server started at epoch={self.started_at:.3f} pid={self.proc.pid} port={self.port}", flush=True)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path, timeout=10, method="GET", body=None):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(self.url(path), method=method, data=body,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        with opener.open(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")

    def wait_ready(self):
        """True when /global/health answers; False (with the reason) on exit or on the bound."""
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False, f"the server exited with code {self.proc.returncode} before it was ready"
            try:
                return True, json.loads(self.get("/global/health", timeout=3))
            except Exception:
                time.sleep(0.25)
        return False, f"OpenCode did not become ready within {READY_TIMEOUT:.0f} s"

    def open_project(self, proj):
        """Start the request that makes OpenCode open the project (and load its plugins), in a thread."""
        def go():
            try:
                self.get("/session?directory=" + urllib.request.quote(str(proj)), timeout=LOAD_TIMEOUT + 30)
            except Exception as e:
                self.open_errors[str(proj)] = repr(e)
        t = threading.Thread(target=go, daemon=True)
        t.start()
        self.threads.append(t)

    def log_text(self):
        parts = []
        for p in (self.out, self.err):
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        return "\n".join(parts)

    def lines(self):
        return parse_log(self.log_text())

    def wait_log(self, pred, bound=None):
        deadline = time.time() + (LOAD_TIMEOUT if bound is None else bound)
        while time.time() < deadline:
            if pred(self.lines()):
                return True
            if self.proc.poll() is not None:
                return pred(self.lines())
            time.sleep(0.5)
        return pred(self.lines())

    def stop(self):
        if self.pgid is not None:
            kill_group(self.pgid)
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass
        with CLEANUP_LOCK:
            LIVE[:] = [x for x in LIVE if x[1] != self.pgid]


LOG_RE = re.compile(r"^timestamp=(\S+) level=(\w+) (.*)$")


def parse_log(text):
    """[(level, message, raw line)] for every structured OpenCode log line; raw (unstructured) lines have level None."""
    out = []
    for raw in text.splitlines():
        m = LOG_RE.match(raw)
        if not m:
            out.append((None, raw, raw))
            continue
        rest = m.group(3)
        mm = re.search(r'message=("(?:[^"\\]|\\.)*"|\S+)', rest)
        msg = mm.group(1) if mm else ""
        if msg.startswith('"'):
            try:
                msg = json.loads(msg)
            except ValueError:
                msg = msg.strip('"')
        out.append((m.group(2), msg, raw))
    return out


def loaded_lines(lines, engine_path):
    return [x for x in lines if LOADED in x[1] and engine_path in x[1] + x[2]]


def missing_warnings(lines, engine_path):
    return [x for x in lines if x[0] == "WARN" and engine_path in x[1] + x[2] and re.search("engine not found", x[1], re.I)
            and re.search("not running", x[1], re.I)]


def noisy_engine_warnings(lines):
    """Warning or error lines that talk about Ratchet's engine being absent (the plugin's loaded line is not one)."""
    return [x for x in lines if x[0] in ("WARN", "ERROR") and re.search("ratchet", x[1], re.I) and LOADED not in x[1]
            and re.search(r"engine not found|not running|missing", x[1], re.I)]


def load_errors(lines):
    return [x for x in lines if x[0] == "ERROR" and re.search(r"plugin|ratchet", x[2], re.I)
            or "failed to load plugin" in x[2]]


# --------------------------------------------------------------------------------------- shared real-OpenCode state

class Load:
    """The one real server shared by T01 to T04, T11 and T12 (and its two projects). Built lazily, once."""
    done = False
    ready = None
    health = None
    server = None
    canary = None
    good = None
    broken = None
    broken_engine = None
    loaded = False
    finished = False
    good_engine = None
    session_id = None
    messages = None
    version = None
    cfg_good = None
    cfg_broken = None
    note = ""


def start_load():
    """Start the server and fire the project-open requests (does not wait for the plugin load)."""
    if Load.done:
        return
    Load.done = True
    s = sc()
    Load.canary = Canary()
    Load.server = Server(s.root, Load.canary)
    Load.server.start()
    ok, info = Load.server.wait_ready()
    Load.ready, Load.health = ok, info
    if not ok:
        Load.note = str(info)
        return
    Load.good = fresh("building", "p-good")
    Load.broken, Load.broken_engine = skill_copy("missing")
    Load.good_engine = plugin_engine_path(Load.good)
    Load.server.open_project(Load.good)
    Load.server.open_project(Load.broken)


def finish_load():
    """Wait (bounded) for both project opens (which include the plugin loads) to answer, then let the log settle and do
    the read-only requests. Idempotent. A bound that runs out leaves Load.loaded False (the cases FAIL with 'plugin
    never loaded')."""
    if Load.finished or not Load.ready:
        return
    Load.finished = True
    srv = Load.server
    deadline = time.time() + LOAD_TIMEOUT
    for t in srv.threads:
        t.join(timeout=max(0.0, deadline - time.time()))
    Load.loaded = not any(t.is_alive() for t in srv.threads) and not srv.open_errors
    if Load.loaded:
        good = Load.good_engine
        srv.wait_log(lambda lines: bool(loaded_lines(lines, good)) or bool(load_errors(lines)), bound=8)
    else:
        Load.note += f" the project open did not finish in {LOAD_TIMEOUT:.0f} s or failed: {srv.open_errors}"
    try:
        Load.version = subprocess.run([OPENCODE, "--version"], capture_output=True, text=True, timeout=60,
                                      env=opencode_env()).stdout.strip()
    except Exception:
        Load.version = None
    q = urllib.request.quote
    try:
        Load.cfg_good = json.loads(srv.get("/config?directory=" + q(str(Load.good)), timeout=60))
        Load.cfg_broken = json.loads(srv.get("/config?directory=" + q(str(Load.broken)), timeout=60))
    except Exception as e:
        Load.note += f" config read failed: {e!r}"
    try:
        made = json.loads(srv.get("/session?directory=" + q(str(Load.good)), timeout=60, method="POST", body=b"{}"))
        Load.session_id = made.get("id")
        Load.messages = json.loads(srv.get(f"/session/{Load.session_id}/message?directory=" + q(str(Load.good)), timeout=60))
    except Exception as e:
        Load.note += f" session create failed: {e!r}"


def require_load(cid):
    """True when the real server is up; otherwise records a plain failing check for the case and returns False."""
    if Load.ready:
        return True
    check(f"[{cid}] the real OpenCode server is running", False, Load.note or "the server never became ready")
    return False


# ------------------------------------------------------------------------------------------------------- the cases

def need_opencode(*cids):
    if OPENCODE:
        return True
    print("NOT RUN: opencode not found", flush=True)
    for c in cids:
        not_run(c, "opencode not found")
    return False


def case_t01():
    print("[CP7-T01] the real OpenCode loads Ratchet's plugin in a throwaway project")
    if not require_load("CP7-T01"):
        return
    finish_load()
    lines = Load.server.lines()
    h = Load.health if isinstance(Load.health, dict) else {}
    check("[CP7-T01] the server reports version 1.18.27", h.get("version") == PIN_VERSION, h)
    check("[CP7-T01] it is the same version `opencode --version` prints", Load.version == h.get("version") == PIN_VERSION,
          (Load.version, h.get("version")))
    check("[CP7-T01] it listens on the chosen port, not the default 4096",
          Load.server.port != 4096 and any(f"listening on http://127.0.0.1:{Load.server.port}" in x[2] for x in lines),
          (Load.server.port, [x[2] for x in lines if "listening" in x[2]][:2]))
    plugs = (Load.cfg_good or {}).get("plugin") or []
    suffix = "/.opencode/plugins/ratchet.js"
    check("[CP7-T01] GET /config?directory=<project> lists the plugin as a file:// URL ending in Ratchet's plugin file",
          any(isinstance(u, str) and u.startswith("file://") and u.endswith(suffix) for u in plugs), plugs,
          key="plugin_listed")
    check("[CP7-T01] the log shows the plugin's init ran (its 'Ratchet plugin loaded' line, with the engine path)",
          bool(loaded_lines(lines, Load.good_engine)),
          (f"plugin never loaded: no '{LOADED}' line for {Load.good_engine}" + ("" if Load.loaded else f" ({Load.note.strip()})")), key="init_logged")
    check("[CP7-T01] the log has no plugin load error line", load_errors(lines) == [], load_errors(lines)[:2])


def case_t02():
    print("[CP7-T02] the plugin's own load line proves it ran inside the real OpenCode runtime")
    if not require_load("CP7-T02"):
        return
    finish_load()
    lines = Load.server.lines()
    mine = loaded_lines(lines, Load.good_engine)
    check("[CP7-T02] exactly one 'Ratchet plugin loaded' line carries the engine path the plugin was installed with",
          len(mine) == 1, f"{len(mine)} lines; OpenCode's own lines seen: {len(lines)}")
    # teeth: a log holding only OpenCode's own lines gives zero matches; a doubled line gives two
    fake_only = parse_log('timestamp=2026-01-01T00:00:00.000Z level=INFO run=x message=loading path=/tmp/x/opencode.json')
    check("[CP7-T02] teeth: a log with only OpenCode's config-loading lines has no loaded line",
          loaded_lines(fake_only, "/e/ratchet.py") == [])
    doubled = parse_log('timestamp=t level=INFO run=x message="Ratchet plugin loaded engine=/e/ratchet.py"\n'
                        'timestamp=t level=INFO run=x message="Ratchet plugin loaded engine=/e/ratchet.py"')
    check("[CP7-T02] teeth: two loaded lines are counted as two (not one)", len(loaded_lines(doubled, "/e/ratchet.py")) == 2)


def case_t03():
    print("[CP7-T03] no model call, credentials or network sign-in is needed or made")
    if not require_load("CP7-T03"):
        return
    finish_load()
    cn = Load.canary
    env = Load.server.env
    check("[CP7-T03] the server's environment holds no *_API_KEY or token variable (built from nothing)",
          env_isolated(env, sc().root, cn.port) == "", env_isolated(env, sc().root, cn.port))
    check("[CP7-T03] teeth: a copy of the ambient environment would be refused",
          env_isolated(dict(os.environ, OPENAI_API_KEY="x"), sc().root, cn.port) != "")
    if not Path("/proc/self/environ").exists():
        print("  NOT RUN: the running server's real environment was not read (no /proc here); only the environment this "
              "test built was checked", flush=True)
    else:
        srv = Load.server
        live = proc_environ(srv.proc.pid) if srv.proc.poll() is None else None
        check("[CP7-T03] the RUNNING server's real environment (/proc/<pid>/environ) has no API key, token or provider "
              "variable, and its HOME and XDG folders are the temp ones",
              bool(live) and env_isolated(live, sc().root, cn.port) == "",
              env_isolated(live, sc().root, cn.port) if live else "the server's environment could not be read")
        leaky = subprocess.Popen(["sleep", "30"], start_new_session=True,
                                 env=dict(server_env(sc().root, cn.port, AMBIENT_PATH), OPENAI_API_KEY="sk-fake-leak"))
        try:
            seen = environ_after_exec(leaky.pid, "sleep")
            check("[CP7-T03] teeth: a server launched with a leaked OPENAI_API_KEY is refused when its real environment is read",
                  bool(seen) and "OPENAI_API_KEY" in seen and env_isolated(seen, sc().root, cn.port) != "",
                  seen or "the leaky child's environment was still empty after 5 s")
        finally:
            kill_group(leaky.pid)
            leaky.wait(timeout=5)
    check("[CP7-T03] a session was created with POST /session and no message was ever posted", Load.session_id is not None,
          Load.note)
    check("[CP7-T03] GET /session/<id>/message is empty", Load.messages == [], Load.messages)
    model = traffic_has_model_request(cn.lines)
    check("[CP7-T03] the recorded traffic has no model request (chat/completions, messages or responses path)",
          model == [], model)
    check("[CP7-T03] teeth: a recorded 'POST /v1/chat/completions' would be caught",
          traffic_has_model_request(["POST http://api.example.com/v1/chat/completions HTTP/1.1"]) != []
          and traffic_has_model_request(["CONNECT api.anthropic.com:443 HTTP/1.1"]) == [])
    auth = sc().home / ".local" / "share" / "opencode" / "auth.json"
    check("[CP7-T03] no auth.json exists afterwards", not auth.exists(), str(auth))
    hosts = cn.hosts()
    print(f"  [CP7-T03] other connection attempts seen through the proxy canary: {len(cn.lines)}"
          + (f" (hosts: {', '.join(sorted(set(hosts)))})" if hosts else ""))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def decoy_snapshot(home):
    snap = {}
    for p in sorted(Path(home).rglob("*")):
        rel = str(p.relative_to(home))
        snap[rel] = ("dir", p.stat().st_mtime_ns) if p.is_dir() else ("file", sha(p), p.stat().st_mtime_ns)
    return snap


DECOY = {}


def make_decoy():
    """Replace this process's HOME by a decoy folder with a sentinel plugin and auth.json, and unset every XDG_*."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-ocsmoke-decoy-")).resolve()
    plug = d / ".config" / "opencode" / "plugins"
    plug.mkdir(parents=True)
    (plug / "decoy.js").write_text('export const Decoy = async (ctx) => { console.error("DECOY_LOADED");\n'
                                   '  try { await ctx.client.app.log({ body: { service: "decoy", level: "warn", '
                                   'message: "DECOY_LOADED" } }); } catch (e) {} return {}; };\n', encoding="utf-8")
    data = d / ".local" / "share" / "opencode"
    data.mkdir(parents=True)
    (data / "auth.json").write_text('{"sentinel": "DECOY_AUTH_SENTINEL"}', encoding="utf-8")
    DECOY["home"] = d
    DECOY["snap"] = decoy_snapshot(d)
    DECOY["orig_home"] = os.environ.get("HOME")
    os.environ["HOME"] = str(d)
    for k in [k for k in os.environ if k.startswith("XDG_")]:
        del os.environ[k]


def case_t04():
    print("[CP7-T04] the developer's real OpenCode config, plugins and sign-in are never touched")
    if not Load.ready and OPENCODE:
        require_load("CP7-T04")
    snap_now = decoy_snapshot(DECOY["home"])
    check("[CP7-T04] this process ran with HOME pointing at the decoy folder",
          os.environ.get("HOME") == str(DECOY["home"]) and not any(k.startswith("XDG_") for k in os.environ))
    diff = sorted(set(DECOY["snap"]) ^ set(snap_now)) + sorted(k for k in DECOY["snap"] if k in snap_now
                                                              and DECOY["snap"][k] != snap_now[k])
    check("[CP7-T04] no file in the decoy home was created, changed or deleted (checksums and mtimes equal)", diff == [], diff)
    log = Load.server.log_text() if Load.server else ""
    check("[CP7-T04] DECOY_LOADED never appears in OpenCode's log or on its stderr", "DECOY_LOADED" not in log)
    s = sc()
    used = [m.group(1) for m in re.finditer(r"(?:path|directory)=(/\S+)", log)]
    outside = sorted({u for u in used if not u.startswith(str(s.root) + os.sep)})
    check("[CP7-T04] every folder named in OpenCode's log is under the test's own temp directory", outside == [], outside[:3])
    check("[CP7-T04] the server's folders (config, data, state) exist only under the temp home",
          (s.home / ".local" / "share" / "opencode").exists() or not Load.ready, str(s.home))
    # teeth: a touched decoy is seen
    probe = DECOY["home"] / ".config" / "opencode" / "plugins" / "decoy.js"
    original = probe.read_bytes()
    st = probe.stat()
    probe.write_bytes(original + b" ")
    os.utime(probe, ns=(st.st_atime_ns, st.st_mtime_ns))
    changed = decoy_snapshot(DECOY["home"]) != DECOY["snap"]
    probe.write_bytes(original)
    os.utime(probe, ns=(st.st_atime_ns, st.st_mtime_ns))
    check("[CP7-T04] teeth: a one-byte change to the decoy plugin (same mtime) is detected by the checksum", changed)
    ghost = DECOY["home"] / "created-by-mutant"
    ghost.write_text("x")
    created = decoy_snapshot(DECOY["home"]) != DECOY["snap"]
    ghost.unlink()
    check("[CP7-T04] teeth: a new file in the decoy home is detected", created)
    # a server that inherited the ambient (decoy) environment would be refused
    check("[CP7-T04] teeth: an environment that kept the decoy HOME is refused",
          env_isolated(dict(server_env(s.root, 1, AMBIENT_PATH), HOME=str(DECOY["home"])), s.root, 1) != "")


def case_t11():
    print("[CP7-T11] a plugin pointing at a missing Ratchet engine says so plainly in OpenCode's log")
    if not require_load("CP7-T11"):
        return
    finish_load()
    lines = Load.server.lines()
    bad = str(Load.broken_engine)
    check("[CP7-T11] setup: the plugin's engine path points at a file that is gone",
          plugin_engine_path(Load.broken) == bad and not Path(bad).exists(), (plugin_engine_path(Load.broken), bad))
    check("[CP7-T11] OpenCode still loads the plugin: the broken project is listed with the plugin and has no load error",
          any(isinstance(u, str) and u.endswith("/.opencode/plugins/ratchet.js") for u in (Load.cfg_broken or {}).get("plugin") or [])
          and [x for x in load_errors(lines) if str(Load.broken) in x[2]] == [], (Load.cfg_broken, load_errors(lines)[:2]))
    warns = missing_warnings(lines, bad)
    check("[CP7-T11] the log carries exactly one warning-level line naming the missing engine path, saying the engine was "
          "not found and Ratchet's protections are not running", len(warns) == 1,
          f"{len(warns)} lines (wanted 1): level WARN, message with 'engine not found', the path, 'not running'")
    err = Load.server.err.read_text(encoding="utf-8", errors="replace") if Load.server.err.exists() else ""
    raw = [x for x in parse_log(err) if x[0] is None and bad in x[1] and re.search("not running", x[1], re.I)]
    check("[CP7-T11] the same message is printed on stderr by console.error (an unstructured line)", len(raw) >= 1,
          f"{len(raw)} such lines on stderr")
    # teeth
    sample = parse_log('timestamp=t level=WARN run=x message="Ratchet engine not found at /e/ratchet.py: Ratchet\'s '
                       'protections are not running."')
    check("[CP7-T11] teeth: the predicate accepts a line of the expected shape and refuses an INFO one and a vague one",
          len(missing_warnings(sample, "/e/ratchet.py")) == 1
          and missing_warnings(parse_log(sample[0][2].replace("level=WARN", "level=INFO")), "/e/ratchet.py") == []
          and missing_warnings(parse_log('timestamp=t level=WARN run=x message="something is wrong /e/ratchet.py"'),
                               "/e/ratchet.py") == []
          and len(missing_warnings(sample + sample, "/e/ratchet.py")) == 2)


def case_t12():
    print("[CP7-T12] with the engine present there is no missing-engine warning")
    if not require_load("CP7-T12"):
        return
    finish_load()
    lines = Load.server.lines()
    check("[CP7-T12] the healthy plugin did load (its loaded line is in the log, so a missing warning would show here)",
          bool(loaded_lines(lines, Load.good_engine)), f"no '{LOADED}' line for {Load.good_engine}")
    mine = [x for x in lines if Load.good_engine in x[1] + x[2] and LOADED not in x[1]
            and re.search(r"engine not found|not running|missing", x[1], re.I)]
    check("[CP7-T12] no 'engine not found' or equivalent warning names the healthy engine path", mine == [], mine[:2])
    check("[CP7-T12] the only Ratchet warning in the log is the broken project's (the healthy one adds no noise)",
          all(str(Load.broken_engine) in x[1] + x[2] for x in noisy_engine_warnings(lines)), noisy_engine_warnings(lines)[:3])


# ---- bun handler cases

def need_bun(*cids):
    if BUN:
        return True
    print("NOT RUN: bun not found", flush=True)
    for c in cids:
        not_run(c, "bun not found")
    return False


class Bun:
    results = {}


def bun_scenarios():
    """Run the guard, prompt and stop scenarios once under bun (cached)."""
    if Bun.results:
        return Bun.results
    r = {}
    g = fresh("building", "bun-guard")
    r["guard"] = (g, drive(BUN, plugin_of(g), g, "guard"))
    p = fresh("waiting", "bun-prompt")
    r["prompt"] = (p, drive(BUN, plugin_of(p), p, "prompt"))
    p2 = fresh("waiting", "bun-prompt-untyped")
    res2 = drive(BUN, plugin_of(p2), p2, "prompt_untyped")
    r["prompt_untyped"] = (p2, res2, engine(p2, "approve-plan").returncode)
    s = fresh("building", "bun-stop")
    r["stop"] = (s, drive(BUN, plugin_of(s), s, "stop"))
    n = fresh("bare", "bun-nogate")
    must(engine(n, "install-hooks", "--host", "opencode", "--scope", "project"))
    r["stop_nogate"] = (n, drive(BUN, plugin_of(n), n, "stop_one"))
    Bun.results = r
    return r


def case_t06():
    print("[CP7-T06] guard handler under bun stops a hand-edit of a protected run file")
    if not need_bun("CP7-T06"):
        return
    proj, res = bun_scenarios()["guard"]
    emit("CP7-T06", v_guard(res))
    m = mutant_plugin(plugin_of(proj), "mut-noguard", "if (refusal !== null) throw new Error(refusal);", "void refusal;")
    check("[CP7-T06] teeth setup: the mutant (guard never throws) was made", m is not None)
    if m is not None:
        teeth("CP7-T06", "a guard that never throws", v_guard(drive(BUN, m, proj, "guard")))
    mut2 = [("the first call did not throw", {"guard": [{"threw": False, "message": None}] + res.get("guard", [])[1:],
                                           "hookNames": ["tool.execute.before"]}),
            ("a blank reason", {"guard": [{"threw": True, "isError": True, "message": " "}] + res.get("guard", [])[1:],
                                "hookNames": ["tool.execute.before"]}),
            ("the ordinary edit also throws", {"guard": res.get("guard", [])[:1] + [{"threw": True, "message": "x"}] + res.get("guard", [])[2:],
                                               "hookNames": ["tool.execute.before"]})]
    for label, bad in mut2:
        teeth("CP7-T06", f"a result where {label}", v_guard(bad))


def case_t07():
    print("[CP7-T07] prompt handler under bun records a typed message and ignores the plugin's own")
    if not need_bun("CP7-T07"):
        return
    r = bun_scenarios()
    proj, res = r["prompt"]
    emit("CP7-T07", v_prompt(res, human_entries(proj)))
    p2, res2, rc = r["prompt_untyped"]
    emit("CP7-T07", v_prompt_untyped(res2, human_entries(p2), rc))
    m = mutant_plugin(plugin_of(proj), "mut-child", 'return parent === undefined || parent === null || parent === "";',
                      "return true;")
    check("[CP7-T07] teeth setup: the mutant (every session counts as top-level) was made", m is not None)
    if m is not None:
        mp = fresh("waiting", "bun-prompt-mut")
        shutil.copy(m, plugin_of(mp).parent / "mut-child.js")
        res_m = drive(BUN, plugin_of(mp).parent / "mut-child.js", mp, "prompt_untyped")
        teeth("CP7-T07", "a plugin that records a sub-agent's message", v_prompt_untyped(res_m, human_entries(mp), 1))


def case_t08():
    print("[CP7-T08] stop handler under bun re-prompts once when a gate is open and never for a child or cancelled session")
    if not need_bun("CP7-T08"):
        return
    r = bun_scenarios()
    proj, res = r["stop"]
    emit("CP7-T08", v_stop(res))
    emit("CP7-T08", v_stop_nogate(r["stop_nogate"][1]))
    m = mutant_plugin(plugin_of(proj), "mut-stopchild",
                      'return parent === undefined || parent === null || parent === "";', "return true;")
    check("[CP7-T08] teeth setup: the mutant (re-prompts a child session) was made", m is not None)
    if m is not None:
        teeth("CP7-T08", "a plugin that re-prompts a sub-agent", v_stop(drive(BUN, m, fresh_copy_with(m, "building"), "stop")))
    m2 = mutant_plugin(plugin_of(proj), "mut-stopabort",
                       'if (!(props.error && props.error.name === "ContextOverflowError")) stopOf(sessionID).stopped = true;',
                       "void 0;")
    check("[CP7-T08] teeth setup: the mutant (a cancel is ignored) was made", m2 is not None)
    if m2 is not None:
        teeth("CP7-T08", "a plugin that ignores a cancelled session", v_stop(drive(BUN, m2, fresh_copy_with(m2, "building"), "stop")))
    m3 = mutant_plugin(plugin_of(proj), "mut-stopnone", "      const answer = await askEngine(STOP_ARGS,", "      return; const answer = await askEngine(STOP_ARGS,")
    check("[CP7-T08] teeth setup: the mutant (never re-prompts) was made", m3 is not None)
    if m3 is not None:
        teeth("CP7-T08", "a plugin that never re-prompts", v_stop(drive(BUN, m3, fresh_copy_with(m3, "building"), "stop")))


def fresh_copy_with(mutant, kind):
    """A fresh project whose plugin file name matches the mutant's (the mutant sits next to the real plugin)."""
    w = fresh(kind, "mut")
    shutil.copy(mutant, plugin_of(w).parent / Path(mutant).name)
    return w


def case_t09():
    print("[CP7-T09] this file's own driver gives identical handler results under bun and under node (it does not read the CP4 to CP6 node tests' results)")
    if not need_bun("CP7-T09"):
        return
    if not NODE:
        not_run("CP7-T09", "node not found")
        print("NOT RUN: node not found")
        return
    table = {"guard": "building", "prompt": "waiting", "stop": "building", "prompt_untyped": "waiting", "stop_one": "bare"}
    diffs = []
    shown = {}
    for scen, kind in table.items():
        pair = {}
        for rt, binary in (("bun", BUN), ("node", NODE)):
            w = fresh(kind, f"par-{rt}-{scen}")
            if kind == "bare":
                must(engine(w, "install-hooks", "--host", "opencode", "--scope", "project"))
            res = drive(binary, plugin_of(w), w, scen)
            res.pop("runtime", None)
            for k in ("logs", "logsAfterInit", "errs", "errsAfterInit"):
                res.pop(k, None)   # what the plugin logs is compared by T10 and T11, not here
            extra = {"entries": [(e.get("prompt"), e.get("session"), e.get("host")) for e in human_entries(w)]}
            pair[rt] = norm({"res": res, "recorded": extra}, w)
        shown[scen] = pair
        if pair["bun"] != pair["node"]:
            diffs.append(scen)
    check("[CP7-T09] every scenario's outcome (blocked / allowed / recorded / not recorded / re-prompt count and the exact "
          "reason text) is equal under bun and under node", diffs == [],
          f"differs in scenario(s): {diffs}: " + (f"bun={shown[diffs[0]]['bun'][:150]} node={shown[diffs[0]]['node'][:150]}" if diffs else ""))
    check("[CP7-T09] the compared outcomes are not empty (the guard blocked, the prompt recorded, the stop re-prompted)",
          '"threw": true' in shown["guard"]["bun"] and "approved" in shown["prompt"]["bun"] and "RATCHET" in shown["stop"]["bun"],
          shown["stop"]["bun"][:200])
    check("[CP7-T09] teeth: two outcomes that differ only in the reason text are told apart",
          norm({"a": "RATCHET GATE: one"}, "/p") != norm({"a": "RATCHET GATE: two"}, "/p")
          and norm({"a": "/p/x"}, "/p") == norm({"a": "/p/x"}, "/p"))


def case_t10():
    print("[CP7-T10] under bun the guard fails open when the engine is missing or broken")
    if not need_bun("CP7-T10"):
        return
    for label, kind in (("missing", "missing"), ("garbage", "garbage")):
        proj, eng = skill_copy(kind)
        res = drive(BUN, plugin_of(proj), proj, "guard")
        g = res.get("guard") or []
        check(f"[CP7-T10] {label} engine: the driver ran and the guard handler resolved for the protected write, the edit and `ls`",
              "crash" not in res and len(g) == 3 and not any(x.get("threw") for x in g), res.get("crash") or g)
        if kind == "missing":
            bad = str(eng)
            warns = [x for x in res.get("logs", []) if x.get("level") == "warn" and bad in (x.get("message") or "")
                     and re.search("engine not found", x.get("message") or "", re.I)
                     and re.search("not running", x.get("message") or "", re.I)]
            check("[CP7-T10] missing engine: the plugin logged the T11 warning (client.app.log, level warn, the path, "
                  "'engine not found', 'not running') exactly once, at load, not once per tool call",
                  len(warns) == 1 and len([x for x in res.get("logsAfterInit", []) if x in warns]) == 1,
                  f"{len(warns)} warnings; logs: {res.get('logs')}")
            check("[CP7-T10] missing engine: the same message went to console.error (stderr)",
                  any(bad in e and re.search("not running", e, re.I) for e in res.get("errs", [])), res.get("errs"))
            check("[CP7-T10] missing engine, plugin in the project's .opencode folder: the warning says to re-run "
                  "install-hooks --host opencode --scope project",
                  len(warns) == 1 and "install-hooks --host opencode --scope project" in warns[0]["message"], warns)
        else:
            check("[CP7-T10] garbage engine: the garbage reply is not taken as a refusal and the plugin logged no "
                  "missing-engine warning (the engine file exists)",
                  not [x for x in res.get("logs", []) if re.search("engine not found", x.get("message") or "", re.I)],
                  res.get("logs"))
    uproj, ueng, uplug, _uenv = user_scope_broken()
    ures = drive(BUN, uplug, uproj, "guard")
    uwarns = [x for x in ures.get("logs", []) if x.get("level") == "warn" and str(ueng) in (x.get("message") or "")]
    check("[CP7-T10] missing engine, plugin in the user's OpenCode folder: the warning says to re-run "
          "install-hooks --host opencode --scope user (not --scope project)",
          len(uwarns) == 1 and "install-hooks --host opencode --scope user" in uwarns[0]["message"]
          and "--scope project" not in uwarns[0]["message"], uwarns or ures.get("crash") or ures.get("logs"))
    ms = mutant_plugin(uplug, "mut-noscope", " --scope ${scope}.", ".")
    check("[CP7-T10] teeth setup: the mutant (a warning that drops the scope) was made", ms is not None)
    if ms is not None:
        mw = [x for x in drive(BUN, ms, uproj, "guard").get("logs", []) if x.get("level") == "warn" and str(ueng) in (x.get("message") or "")]
        check("[CP7-T10] teeth: a warning that drops the scope would be caught by the user-scope check above",
              not (len(mw) == 1 and "install-hooks --host opencode --scope user" in mw[0]["message"]), mw)
    # teeth: a plugin that does not fail open is refused (the guard throws when the engine answers nothing)
    proj, _eng = skill_copy("missing")
    m = mutant_plugin(plugin_of(proj), "mut-failclosed", "        refusal = null;\n      }\n      if (refusal !== null)",
                      '        refusal = "closed";\n      }\n      if (refusal === null) refusal = "closed";\n      if (refusal !== null)')
    check("[CP7-T10] teeth setup: the mutant (fails closed) was made", m is not None)
    if m is not None:
        mres = drive(BUN, m, proj, "guard")
        mg = mres.get("guard") or []
        check("[CP7-T10] teeth: a plugin that blocks every tool when the engine is missing would be caught",
              any(x.get("threw") for x in mg), mg)


# ---- doctor cases

def doctor(proj):
    return subprocess.run([sys.executable, str(ENGINE), "doctor"], cwd=str(proj), capture_output=True, text=True,
                          env=engine_env(), timeout=180)


def broken_flagged(out, eng):
    """Does doctor's output flag the OpenCode plugin as broken with the contract's three facts on one line?"""
    return [ln for ln in out.splitlines() if eng in ln and re.search("missing", ln, re.I)
            and "install-hooks --host opencode" in ln]


def scope_flagged(out, eng, scope):
    """The broken-plugin lines that also name the fix with the scope the plugin was found in."""
    return [ln for ln in broken_flagged(out, eng) if re.search(rf"install-hooks --host opencode --scope {scope}\b", ln)]


def user_scope_broken():
    """A project with no plugin of its own and a USER-scope plugin (in an OPENCODE_CONFIG_DIR folder under the temp root,
    not the temp home, so the real server never loads it) installed from a skill copy whose ratchet.py is then deleted.
    Returns (project, engine path, plugin file, environment for doctor)."""
    s = sc()
    skill = s.root / s.nxt("skill-ugone")
    shutil.copytree(ENGINE.parent, skill / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ENGINE.parent.parent / "assets", skill / "assets")
    eng = skill / "scripts" / "ratchet.py"
    proj = s.proj / s.nxt("p-ugone")
    shutil.copytree(template("bare"), proj, symlinks=True)
    cfg = s.root / s.nxt("ocfg")
    env = engine_env(OPENCODE_CONFIG_DIR=str(cfg))
    must(subprocess.run([sys.executable, str(eng), "install-hooks", "--host", "opencode", "--scope", "user"], cwd=str(proj),
                        capture_output=True, text=True, env=env, timeout=180))
    eng.unlink()
    return proj, eng, cfg / "plugins" / "ratchet.js", env


class Doc:
    broken = None
    broken_eng = None


def case_t13():
    print("[CP7-T13] doctor flags the OpenCode plugin as broken when its engine path is gone")
    if Doc.broken is None:
        Doc.broken, Doc.broken_eng = skill_copy("missing")
    proj, eng = Doc.broken, str(Doc.broken_eng)
    r = doctor(proj)
    out = (r.stdout or "") + (r.stderr or "")
    row = [ln for ln in out.splitlines() if ln.strip().startswith("opencode")]
    check("[CP7-T13] setup: the engine file is gone and the plugin still names it",
          plugin_engine_path(proj) == eng and not Path(eng).exists(), (plugin_engine_path(proj), eng))
    check("[CP7-T13] doctor exits normally (0)", r.returncode == 0, (r.returncode, out[-300:]))
    check("[CP7-T13] the opencode row still shows where the plugin is installed",
          len(row) == 1 and ".opencode/plugins/ratchet.js" in row[0], row)
    check("[CP7-T13] a plain line says the plugin's Ratchet engine is missing, names the path and the fix "
          "(re-run install-hooks --host opencode)", len(broken_flagged(out, eng)) >= 1,
          f"no line with the engine path, 'missing' and 'install-hooks --host opencode'; opencode row: {row}")
    healthy = fresh("building", "doc-teeth")
    ho = doctor(healthy).stdout
    check("[CP7-T13] teeth: the same predicate finds nothing in a healthy install's doctor output",
          broken_flagged(ho, str(ENGINE)) == [] and broken_flagged(ho, eng) == [])
    # the fix command carries the scope the plugin was found in (install-hooks defaults to project scope)
    check("[CP7-T13] project-scope plugin: the line says '--scope project' and never '--scope user'",
          len(scope_flagged(out, eng, "project")) >= 1 and scope_flagged(out, eng, "user") == [], broken_flagged(out, eng))
    uproj, ueng, uplug, uenv = user_scope_broken()
    ur = subprocess.run([sys.executable, str(ENGINE), "doctor"], cwd=str(uproj), capture_output=True, text=True, env=uenv,
                        timeout=180)
    uout = (ur.stdout or "") + (ur.stderr or "")
    check("[CP7-T13] user-scope setup: the plugin is in the user config folder, the project has none, the engine is gone",
          uplug.is_file() and not plugin_of(uproj).exists() and not Path(ueng).exists(), (str(uplug), str(ueng)))
    check("[CP7-T13] user-scope plugin: the line says '--scope user' (and never '--scope project')",
          len(scope_flagged(uout, str(ueng), "user")) >= 1 and scope_flagged(uout, str(ueng), "project") == [],
          broken_flagged(uout, str(ueng)) or uout[-300:])
    check("[CP7-T13] teeth: a line that drops the scope, or names the wrong one, is refused by the scope predicate",
          scope_flagged(uout.replace(" --scope user", ""), str(ueng), "user") == []
          and scope_flagged(uout.replace("--scope user", "--scope project"), str(ueng), "user") == []
          and scope_flagged(out.replace(" --scope project", ""), eng, "project") == [])


def case_t14():
    print("[CP7-T14] doctor does not flag a healthy OpenCode install")
    healthy = fresh("building", "doc-healthy")
    out = doctor(healthy)
    text = (out.stdout or "") + (out.stderr or "")
    row = [ln for ln in text.splitlines() if ln.strip().startswith("opencode")]
    check("[CP7-T14] healthy project: doctor lists the plugin location", len(row) == 1 and ".opencode/plugins/ratchet.js" in row[0], row)
    check("[CP7-T14] healthy project: no 'missing' or 'broken' wording anywhere in doctor's output",
          not re.search(r"missing|broken", text, re.I), re.findall(r".*(?:missing|broken).*", text, re.I)[:2])
    check("[CP7-T14] healthy project: doctor exits 0", out.returncode == 0, out.returncode)
    bare = fresh("bare", "doc-bare")
    o2 = doctor(bare)
    t2 = (o2.stdout or "") + (o2.stderr or "")
    row2 = [ln for ln in t2.splitlines() if ln.strip().startswith("opencode")]
    check("[CP7-T14] project with no plugin: the opencode row says 'not installed'", len(row2) == 1 and "not installed" in row2[0], row2)
    check("[CP7-T14] project with no plugin: no broken flag", not re.search(r"missing|broken", t2, re.I), t2[-200:])
    # control: the flag is discriminating. The same doctor must flag the broken project, or "no flag" above proves nothing.
    if Doc.broken is None:
        Doc.broken, Doc.broken_eng = skill_copy("missing")
    bt = doctor(Doc.broken).stdout or ""
    check("[CP7-T14] control: the same doctor does flag a broken install (so the healthy result above is a real 'no flag')",
          len(broken_flagged(bt, str(Doc.broken_eng))) >= 1, "doctor never flags a missing engine")
    check("[CP7-T14] teeth: an output with 'OpenCode plugin engine missing' added is refused by the healthy predicate",
          re.search(r"missing|broken", text + "\n  opencode plugin engine missing", re.I) is not None)


# ---- the meta cases: this script run again in a child

def child_run(mode, groups, path, extra_env=None, timeout=420):
    s = sc()
    env = dict(os.environ)
    env.update(RATCHET_SMOKE_CHILD=mode, RATCHET_SMOKE_GROUPS=",".join(groups), PATH=path, PYTHONDONTWRITEBYTECODE="1")
    env.pop("RATCHET_TEST_REPORT", None)
    env.update(extra_env or {})
    t0 = time.time()
    proc = subprocess.Popen([sys.executable, str(THIS_FILE)], cwd=str(s.work), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        out, err = end_child(proc)
        raise subprocess.TimeoutExpired(proc.args, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err), time.time() - t0


def end_child(proc):
    """A meta child that ran past its bound: SIGTERM to its process group first (its handler runs its clean-up, which
    kills its server), SIGKILL after a grace, then SIGKILL the group of anything still naming the child's temp root (its
    `opencode serve` has a session of its own, so killing the child's group alone would orphan it)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        kill_group(proc.pid)
        out, err = proc.communicate(timeout=10)
    root = re.search(r"\[smoke\] temp root: (\S+)", out or "")
    for pid in (procs_mentioning(root.group(1)) if root else []):
        try:
            pg = os.getpgid(pid)
            if pg != os.getpgid(0):
                kill_group(pg)
        except OSError:
            pass
    return out, err


def path_dir(names):
    s = sc()
    d = s.root / s.nxt("pathdir")
    d.mkdir()
    for name in names:
        src = sys.executable if name == "python3" else shutil.which(name, path=AMBIENT_PATH)
        if src:
            (d / name).symlink_to(src)
    return str(d)


def case_t15():
    print("[CP7-T15] a missing opencode shows as not run, never as passed")
    path = path_dir(["python3", "node", "git"])
    # only the OpenCode-dependent groups: the exact "0 passed, 0 failed" below then holds whether or not bun is installed
    r, _t = child_run("noopencode", ["load", "lifecycle"], path, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    lines = out.splitlines()
    want = ["CP7-T01", "CP7-T02", "CP7-T03", "CP7-T04", "CP7-T05", "CP7-T11", "CP7-T12"]
    check("[CP7-T15] a plain line 'NOT RUN: opencode not found' is printed on its own", "NOT RUN: opencode not found" in lines, out[-400:])
    check("[CP7-T15] each load case (T01 to T05, T11, T12) is listed as NOT RUN with that reason",
          all(any(l.strip() == f"NOT RUN: opencode not found [{c}]" for l in lines) for c in want),
          [c for c in want if not any(l.strip() == f"NOT RUN: opencode not found [{c}]" for l in lines)])
    m = re.search(r"(\d+) passed, (\d+) failed, (\d+) not run", out)
    check("[CP7-T15] the summary counts them as not run and not as passed (0 passed, 0 failed, at least 7 not run)",
          bool(m) and m.group(1) == "0" and m.group(2) == "0" and int(m.group(3)) >= 7, m.group(0) if m else out[-300:])
    check("[CP7-T15] the exit code is 0 (the documented one for 'not run')", r.returncode == 0, r.returncode)
    check("[CP7-T15] no 'plugin loaded' success line is printed anywhere",
          not re.search(r"✓.*plugin loaded|plugin loaded by the real", out, re.I) and "✓" not in out, out[-300:])
    check("[CP7-T15] teeth: a NOT RUN run can never look like a pass (no ✓ lines, and the counts line is exact)",
          m is not None and "✓" not in out)


def case_t16():
    print("[CP7-T16] a missing bun shows as not run, never as passed")
    if not need_opencode("CP7-T16"):
        return
    path = path_dir(["python3", "node", "git", "opencode"])
    r, _t = child_run("nobun", ["load", "bun"], path, timeout=LOAD_TIMEOUT + READY_TIMEOUT + 240)
    out = (r.stdout or "") + (r.stderr or "")
    lines = out.splitlines()
    check("[CP7-T16] a plain line 'NOT RUN: bun not found' is printed", "NOT RUN: bun not found" in lines, out[-400:])
    want = ["CP7-T06", "CP7-T07", "CP7-T08", "CP7-T09", "CP7-T10"]
    check("[CP7-T16] each handler case (T06 to T10) is listed as NOT RUN with that reason",
          all(any(l.strip() == f"NOT RUN: bun not found [{c}]" for l in lines) for c in want), out[-300:])
    check("[CP7-T16] the real-OpenCode load cases still ran and are reported (checks printed for T01, with no "
          "'opencode not found')", "NOT RUN: opencode not found" not in lines
          and any("[CP7-T01]" in l and (l.strip().startswith("✓") or l.strip().startswith("✗")) for l in lines), out[-300:])
    m = re.search(r"(\d+) passed, (\d+) failed, (\d+) not run", out)
    check("[CP7-T16] the summary counts the five handler cases as not run (at least 5)", bool(m) and int(m.group(3)) >= 5,
          m.group(0) if m else out[-300:])
    check("[CP7-T16] the summary headline says handlers were not exercised", bool(re.search(r"handlers were not exercised", out, re.I)),
          out[-300:])


FAKE_OPENCODE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo 1.18.27; exit 0; fi
sleep 600 &
sleep 600 &
wait
"""


def case_t17():
    print("[CP7-T17] a server that never becomes ready fails the test within its bound and is killed")
    s = sc()
    fake = s.root / s.nxt("fakeoc")
    fake.mkdir()
    exe = fake / "opencode"
    exe.write_text(FAKE_OPENCODE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    bound = 6
    path = os.pathsep.join([str(fake), AMBIENT_PATH])
    r, took = child_run("hang", ["load"], path, extra_env={"RATCHET_SMOKE_READY_TIMEOUT": str(bound)}, timeout=240)
    out = (r.stdout or "") + (r.stderr or "")
    ts = re.search(r"server started at epoch=([0-9.]+)", out)
    root = re.search(r"\[smoke\] temp root: (\S+)", out)
    check("[CP7-T17] the run exits non-zero", r.returncode != 0, r.returncode)
    check("[CP7-T17] the message says OpenCode did not become ready", "OpenCode did not become ready" in out, out[-400:])
    check("[CP7-T17] it is not reported as passed or as not run", "NOT RUN: opencode" not in out
          and not re.search(r"✓ \[CP7-T0[12]\].*(loaded|ran)", out), out[-300:])
    ended = time.time()
    check("[CP7-T17] it ended within the bound plus a small grace after the server started",
          bool(ts) and (ended - float(ts.group(1))) <= bound + 30 and took <= 240, (ts.group(1) if ts else None, took))
    left = procs_mentioning(root.group(1)) if root else ["no temp root was reported"]
    check("[CP7-T17] the hung fake server and its children are gone (no process still mentions the run's temp folder)",
          left == [], left)


def case_t05():
    print("[CP7-T05] the server and all its children are gone after every run, pass or fail")
    if not need_opencode("CP7-T05"):
        return
    s = sc()
    # teeth first: the liveness predicates see a live group and a live process that mentions a folder
    probe = subprocess.Popen(["sleep", "60"], start_new_session=True, env=dict(os.environ, SMOKE_PROBE=str(s.root)))
    try:
        check("[CP7-T05] teeth: a live process group is seen as live", group_members(probe.pid) == [probe.pid])
        check("[CP7-T05] teeth: a live process whose environment mentions the temp folder is found",
              probe.pid in procs_mentioning(str(s.root)))
    finally:
        kill_group(probe.pid)
        probe.wait(timeout=5)
    check("[CP7-T05] teeth: after the kill neither predicate sees it", group_members(probe.pid) == []
          and probe.pid not in procs_mentioning(str(s.root)))
    for mode in ("normal", "fail", "term"):
        state = s.work / f"life-{mode}.json"
        env = dict(RATCHET_SMOKE_STATE_FILE=str(state), RATCHET_SMOKE_READY_TIMEOUT=str(READY_TIMEOUT))
        genv = dict(os.environ)
        genv.update(RATCHET_SMOKE_CHILD=f"lifecycle-{mode}", RATCHET_SMOKE_GROUPS="lifecycle", PYTHONDONTWRITEBYTECODE="1")
        genv.update(env)
        t0 = time.time()
        proc = subprocess.Popen([sys.executable, str(THIS_FILE)], cwd=str(s.work), env=genv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            if mode == "term":
                deadline = time.time() + READY_TIMEOUT + 60
                while time.time() < deadline and not (state.exists() and '"ready": true' in state.read_text()):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.25)
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
            try:
                out, _ = proc.communicate(timeout=READY_TIMEOUT + 90)
                ended_in_time = True
            except subprocess.TimeoutExpired:
                ended_in_time = False
                kill_group(proc.pid)
                try:
                    server_pg = json.loads(state.read_text()).get("pgid")
                except (OSError, ValueError):
                    server_pg = None
                if server_pg:
                    kill_group(server_pg)   # the server has a process group of its own
                out, _ = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                kill_group(proc.pid)
        info = json.loads(state.read_text()) if state.exists() else {}
        check(f"[CP7-T05] {mode}: the run reached a ready server and told the parent where it was",
              bool(info.get("ready")), (info, out[-300:]))
        check(f"[CP7-T05] {mode}: the run ended inside its time bound", ended_in_time, round(time.time() - t0))
        if mode == "normal":
            check("[CP7-T05] normal: the run exited 0", proc.returncode == 0, (proc.returncode, out[-200:]))
        else:
            check(f"[CP7-T05] {mode}: the run did not exit 0 (a forced failure / SIGTERM is not a pass)", proc.returncode != 0,
                  proc.returncode)
        gone = False
        if info.get("pgid"):
            end = time.time() + 15
            while time.time() < end and group_members(info["pgid"]):
                time.sleep(0.2)
            gone = group_members(info["pgid"]) == []
        check(f"[CP7-T05] {mode}: the server's process group has no live member", gone, group_members(info.get("pgid") or 0))
        check(f"[CP7-T05] {mode}: the chosen port refuses connections", bool(info.get("port")) and port_refuses(info["port"]), info.get("port"))
        left = procs_mentioning(info["root"]) if info.get("root") else ["no root reported"]
        check(f"[CP7-T05] {mode}: no process mentioning the run's temp directory is left", left == [], left)
    t05_leak_tooth()


def t05_leak_tooth():
    """A run that skips its clean-up leaves a live server: the T05 observations must see it (then it is killed here)."""
    s = sc()
    state = s.work / "life-leak.json"
    genv = dict(os.environ)
    genv.update(RATCHET_SMOKE_CHILD="lifecycle-leak", RATCHET_SMOKE_GROUPS="lifecycle", PYTHONDONTWRITEBYTECODE="1",
                RATCHET_SMOKE_STATE_FILE=str(state))
    proc = subprocess.Popen([sys.executable, str(THIS_FILE)], cwd=str(s.work), env=genv, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    info = {}
    try:
        proc.communicate(timeout=READY_TIMEOUT + 90)
        info = json.loads(state.read_text()) if state.exists() else {}
        alive = bool(info.get("pgid")) and group_members(info["pgid"]) != []
        check("[CP7-T05] teeth: a run that skips its clean-up leaves a live process group, and the check sees it", alive, info)
        check("[CP7-T05] teeth: and its port still accepts connections, and a process mentioning its temp folder is found",
              bool(info.get("port")) and not port_refuses(info["port"]) and procs_mentioning(info.get("root", "?")) != [], info)
    finally:
        if info.get("pgid"):
            kill_group(info["pgid"])
        if proc.poll() is None:
            kill_group(proc.pid)


def lifecycle(mode):
    """The child side of T05: start the server, report it, then end in the way `mode` says, cleaning up in finally."""
    s = sc()
    cn = Canary()
    srv = Server(s.root, cn)
    state = Path(os.environ["RATCHET_SMOKE_STATE_FILE"])
    try:
        srv.start()
        ok, info = srv.wait_ready()
        state.write_text(json.dumps({"ready": bool(ok), "pgid": srv.pgid, "port": srv.port, "root": str(s.root)}))
        if not ok:
            print(f"OpenCode did not become ready: {info}")
            sys.exit(1)
        if mode == "leak":
            os._exit(0)   # a mutant run that skips its clean-up: only used to show the T05 observation can fail
        if mode == "fail":
            raise AssertionError("forced assertion failure in the middle of a smoke run")
        if mode == "term":
            end = time.time() + 120
            while time.time() < end:
                time.sleep(0.2)
            sys.exit(1)
    finally:
        srv.stop()
        cn.close()
        kill_all()


# ---- the report

def versions():
    def ver(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=opencode_env()).stdout.strip().split("\n")[0]
        except Exception:
            return "unknown"
    return {"opencode": ver([OPENCODE, "--version"]) if OPENCODE else None, "bun": ver([BUN, "--version"]) if BUN else None,
            "node": ver([NODE, "--version"]) if NODE else None}


def build_report(vs, attempts):
    ex = []
    if KEYS.get("plugin_listed"):
        ex.append("the plugin file (.opencode/plugins/ratchet.js) was found and listed in its config by the real OpenCode "
                  f"{vs['opencode']}")
    if KEYS.get("init_logged"):
        ex.append("the real OpenCode loaded it, ran its init, and its client.app.log write ('Ratchet plugin loaded') reached "
                  "OpenCode's log")
    if all(r in RESULTS and all(ok for ok, _n, _d in RESULTS[r]) for r in ("CP7-T06", "CP7-T07", "CP7-T08")):
        ex.append("the guard, prompt and stop handlers ran under bun with a fake SDK client and the real ratchet.py engine")
    lines = ["", "REPORT: what this smoke run did and did not exercise",
             f"Versions seen: opencode {vs['opencode']}, bun {vs['bun']}, node {vs['node']}", "Exercised:"]
    lines += [f"  - {x}" for x in ex] or ["  - nothing could be confirmed (see the failed checks above)"]
    lines += ["Not exercised:",
              "  - no live chat turn was run",
              "  - OpenCode itself never fired tool.execute.before, chat.message or session.idle: the handlers were called "
              "by this test, under bun, not dispatched by OpenCode",
              "  - no model call was made and no credentials were used",
              "  - the user's own ! shell commands (they never reach plugin hooks)",
              f"  - network: {attempts} connection attempt(s) were seen through the proxy canary (counted in T03); "
              "connections that bypass proxies are not seen"]
    return "\n".join(lines) + "\n"


def sections(text):
    ex, no, cur = [], [], None
    for ln in text.splitlines():
        if ln.startswith("Exercised:"):
            cur = ex
        elif ln.startswith("Not exercised:"):
            cur = no
        elif ln.strip() and cur is not None and ln.startswith("  "):
            cur.append(ln.strip())
    return ex, no


def report_problems(text, attempts):
    """Why this report is not acceptable, as a list (empty when it is)."""
    bad = []
    for tool in ("opencode", "bun", "node"):
        if not re.search(rf"\b{tool}\s+v?\d+\.\d+\.\d+", text):
            bad.append(f"no {tool} version")
    ex, no = sections(text)
    exj, noj = " ".join(ex).lower(), " ".join(no).lower()
    for label, needle in (("the plugin file found and loaded by the real OpenCode", "found and listed"),
                          ("its init and log write", "ran its init"),
                          ("the handlers run under bun with a fake client and the real engine", "under bun with a fake sdk client and the real")):
        if needle not in exj:
            bad.append(f"Exercised lacks: {label}")
    for label, needle in (("no live chat turn", "no live chat turn"), ("OpenCode never fired tool.execute.before", "never fired tool.execute.before"),
                          ("chat.message", "chat.message"), ("session.idle", "session.idle"), ("no model call", "no model call"),
                          ("the user's own ! shell commands", "! shell commands"), ("the network attempts", f"{attempts} connection attempt")):
        if needle not in noj:
            bad.append(f"Not exercised lacks: {label}")
    for ln in ex:
        if re.search(r"dispatch|fired|opencode (?:called|invoked|ran the handler)", ln, re.I):
            bad.append(f"Exercised claims OpenCode dispatched a handler: {ln[:80]}")
    for ln in no:
        if re.search(r"dispatch|fired", ln, re.I) and not re.search(r"never|not|no ", ln, re.I):
            bad.append(f"a Not exercised line claims dispatch: {ln[:80]}")
    return bad


def case_t18(vs, attempts, text):
    print("[CP7-T18] the report says plainly what was and was not exercised, with versions")
    probs = report_problems(text, attempts)
    check("[CP7-T18] the report lists the versions and an Exercised part naming the loaded plugin file, its init and log "
          "write, and the handlers under bun; and a Not exercised part naming the live turn, OpenCode never firing the "
          "handlers, the model call, the ! shell commands and the network attempts", probs == [], probs)
    check("[CP7-T18] it never claims OpenCode dispatched a handler", not [p for p in probs if "dispatch" in p], probs)
    good = ("Versions seen: opencode 1.18.27, bun 1.4.0, node v26.7.0\nExercised:\n"
            "  - the plugin file was found and listed in its config by the real OpenCode\n"
            "  - the real OpenCode loaded it, ran its init, and its client.app.log write reached OpenCode's log\n"
            "  - the guard, prompt and stop handlers ran under bun with a fake SDK client and the real ratchet.py engine\n"
            "Not exercised:\n  - no live chat turn was run\n"
            "  - OpenCode itself never fired tool.execute.before, chat.message or session.idle\n"
            "  - no model call was made\n  - the user's own ! shell commands\n"
            f"  - network: {attempts} connection attempt(s) were seen\n")
    muts = {
        "a report with no versions": good.replace("Versions seen: opencode 1.18.27, bun 1.4.0, node v26.7.0\n", ""),
        "a report missing the handlers line": good.replace("  - the guard, prompt and stop handlers ran under bun with a fake SDK client and the real ratchet.py engine\n", ""),
        "a report missing the init line": good.replace("  - the real OpenCode loaded it, ran its init, and its client.app.log write reached OpenCode's log\n", ""),
        "a report missing the ! shell limit": good.replace("  - the user's own ! shell commands\n", ""),
        "a report missing the network count": good.replace(f"  - network: {attempts} connection attempt(s) were seen\n", ""),
        "a report that says OpenCode dispatched the handlers": good.replace("ran under bun with a fake SDK client",
                                                                          "were dispatched by OpenCode with a fake SDK client"),
        "a report that drops the not-fired statement": good.replace("OpenCode itself never fired tool.execute.before, chat.message or session.idle",
                                                                   "tool.execute.before, chat.message or session.idle"),
    }
    check("[CP7-T18] teeth: a reference report of the right shape is accepted", report_problems(good, attempts) == [],
          report_problems(good, attempts))
    for label, mt in muts.items():
        check(f"[CP7-T18] teeth: {label} is refused", report_problems(mt, attempts) != [], "accepted")


# --------------------------------------------------------------------------------------------------------- main

def run_case(fn, *args):
    try:
        fn(*args)
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        cid = "CP7-T" + fn.__name__[-2:] if fn.__name__.startswith("case_t") else "CP7-T01"
        check(f"[{cid}] the case ran to its end without a crash in the test itself", False, tb[-500:])


def main():
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    threading.Thread(target=watchdog, daemon=True).start()
    atexit.register(kill_all)
    s = sc()
    print(f"[smoke] temp root: {s.root}", flush=True)
    make_decoy()
    if CHILD.startswith("lifecycle-"):
        try:
            lifecycle(CHILD.split("-", 1)[1])
        finally:
            kill_all()
        return 0
    try:
        want_load = "load" in GROUPS or "report" in GROUPS
        if want_load and OPENCODE:
            run_case(start_load)
            if not Load.ready:
                print(f"  ✗ [CP7-T01] {Load.note}", flush=True)
        print("== handler cases under bun, doctor cases (the real OpenCode loads the plugins meanwhile)")
        if "bun" in GROUPS:
            for fn in (case_t06, case_t07, case_t08, case_t09, case_t10):
                run_case(fn)
        if "doctor" in GROUPS:
            for fn in (case_t13, case_t14):
                run_case(fn)
        if "load" in GROUPS:
            print("== the real OpenCode (waiting for the plugin loads, up to the bound)")
            if need_opencode("CP7-T01", "CP7-T02", "CP7-T03", "CP7-T04", "CP7-T11", "CP7-T12"):
                for fn in (case_t01, case_t02, case_t03, case_t11, case_t12):
                    run_case(fn)
        if "lifecycle" in GROUPS:
            run_case(case_t05)
        if "meta" in GROUPS:
            for fn in (case_t15, case_t16, case_t17):
                run_case(fn)
        if "load" in GROUPS and OPENCODE:
            run_case(case_t04)
    finally:
        if Load.server:
            Load.server.stop()
        if Load.canary:
            Load.canary.close()
        kill_all()
    vs = versions()
    attempts = len(Load.canary.lines) if Load.canary else 0
    if CHILD == "":
        full = bool(OPENCODE and BUN and NODE)
        text = build_report(vs, attempts)
        if full:
            case_t18(vs, attempts, text)
        else:
            missing = [n for n, p in (("opencode", OPENCODE), ("bun", BUN), ("node", NODE)) if not p]
            print(f"NOT RUN: {' and '.join(missing)} not found")
            not_run("CP7-T18", f"{' and '.join(missing)} not found")
        print(text)
    else:
        text = build_report(vs, attempts)
        print(text)
    ran = {c for g, cs in GROUP_OF.items() if g in GROUPS for c in cs}
    if CHILD:
        ran.discard("CP7-T18")
    for c in sorted(ran):
        if c not in RESULTS and c not in NOT_RUN:
            check(f"[{c}] the case produced a result", False, "the case never ran")
    print("\nPer case: " + ", ".join(
        f"{c[4:]} {'NOT RUN' if c in NOT_RUN else ('PASS' if all(ok for ok, _n, _d in RESULTS[c]) else 'FAIL')}"
        for c in sorted(ran)))
    nr = len(NOT_RUN)
    if "bun not found" in NOT_RUN.values() or any(v.startswith("bun") for v in NOT_RUN.values()):
        print("Summary: handlers were not exercised (bun not found), so the guard, prompt and stop cases prove nothing in this run.")
    if nr and not (PASS or FAIL):
        print("NOT RUN: nothing was exercised in this run")
    print(f"\n{PASS} passed, {FAIL} failed, {nr} not run", flush=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        kill_all()
    sys.exit(code)
