#!/usr/bin/env python3
"""
OpenCode support, step 3: `ratchet.py install-hooks --host opencode` puts Ratchet's (not yet active) plugin file in
OpenCode's plugins folder, `uninstall` removes only that file, `doctor` lists OpenCode, `install.sh --host opencode`
ships the plugin template and installs the plugin, `ratchet.py hook guard|prompt|stop --host opencode` answers with ONE
JSON reply ({"block": bool, "message": str}, exit 0), and the README table and test list know about it.

    python3 tests/test_hook_opencode_install.py      # builds throwaway git projects with a temp HOME

Every case runs the real `ratchet.py` (or `install.sh`) as a subprocess in a throwaway project with a throwaway HOME,
and a separate throwaway RATCHET_HOME. Every environment variable the engine's host detection reads (CODEX_*,
CLAUDECODE, GEMINI_*, COPILOT_*, RATCHET_*) and every OpenCode or XDG one is removed from the child environment, then
XDG_CONFIG_HOME / OPENCODE_CONFIG_DIR are set only by the cases that test them, so a user-scope install can never touch
the real ~/.config/opencode. This file never loads ratchet.py as a module and never reads its source text (the README
table fact, HOST_ENFORCEMENT, is read by running the engine file in a child process), so it needs no test-inventory
entry.

Where OpenCode looks for plugins (checked 2026-10-31 against github.com/anomalyco/opencode tag v1.19.5, commit
810c0eb; docs https://opencode.ai/docs/plugins/):
  * project plugins live in `.opencode/plugins/` (the `.opencode` folder is found by walking up from the project folder
    to the git worktree root; packages/opencode/src/config/paths.ts `directories()`);
  * the global config folder is `Global.Path.config` (the docs and the code agree on `~/.config/opencode`, moved by
    XDG_CONFIG_HOME to `$XDG_CONFIG_HOME/opencode`), and OPENCODE_CONFIG_DIR, when set, is one MORE config folder that
    is scanned in addition (config/paths.ts line 39: `Flag.OPENCODE_CONFIG_DIR ? [Flag.OPENCODE_CONFIG_DIR] : []`,
    config/config.ts lines 430-450 loop over every folder `directories()` returns).
  So the rule this file asserts for `--scope user`, the one Ratchet installs into, is: OPENCODE_CONFIG_DIR/plugins when
  OPENCODE_CONFIG_DIR is set (an explicit choice of the user wins), else $XDG_CONFIG_HOME/opencode/plugins when
  XDG_CONFIG_HOME is set, else ~/.config/opencode/plugins. Not confirmed from source: how Global.Path.config reads
  XDG_CONFIG_HOME (the shell closed before that file could be read; it is the xdg-basedir default the docs describe),
  and which of the two folders OpenCode prefers when both are set (it loads from both, so either target works for the
  user; this file pins "OPENCODE_CONFIG_DIR wins" as the test-writer's choice and says so).

Each case id appears in its check names as [CP3-Txx]. When RATCHET_TEST_REPORT is set, one JSON entry per planned case
({"cases": [{"id", "status", "message"}]}) is written there.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
SKILL = ROOT / "skills" / "ratchet-orchestrator"
INSTALL_SH = ROOT / "install.sh"
README = ROOT / "README.md"
THIS_FILE = Path(__file__).resolve()
NEW_TEST_LINE = "python3 tests/test_hook_opencode_install.py"
HEADER = "// Ratchet Loop OpenCode plugin, written by ratchet.py install-hooks"
PLANNED = [f"CP3-T{n:02d}" for n in range(1, 24)]
PASS, FAIL = 0, 0
RESULTS = {}   # case id -> [(ok, name, detail)]
CRASHED = {}   # case id -> what crashed

# Every variable a host-detection rule, a CLI home folder or an OpenCode or XDG path could be read from.
_STRIP_PREFIXES = ("CODEX_", "OPENCODE_", "XDG_", "RATCHET_", "GEMINI_", "COPILOT_", "GITHUB_COPILOT_", "CLAUDE")

ALREADY_RE = re.compile(r"already|unchanged|up\s+to\s+date|up-to-date|nothing\s+to\s+(?:do|change)|no\s+change", re.I)
NOT_RATCHETS_RE = re.compile(r"not\s+(?:a\s+|one\s+of\s+|written\s+by\s+)?ratchet|isn['’]t\s+(?:a\s+)?ratchet"
                             r"|no\s+ratchet\s+header|without\s+(?:the\s+)?ratchet", re.I)
LEFT_ALONE_RE = re.compile(r"left\s+(?:it\s+)?alone|not\s+(?:been\s+)?(?:overwrit|remov|delet|touch|modif|chang)"
                           r"|(?:won['’]t|will\s+not|did\s+not|didn['’]t|was\s+not|wasn['’]t)\s+"
                           r"(?:overwrit|remov|delet|touch|modif|chang)|untouched|left\s+(?:in\s+place|as\s+is|unchanged)"
                           r"|kept", re.I)
USER_CODE = b"export const MyPlugin = async () => ({});\n"


def check(name, cond, detail=""):
    global PASS, FAIL
    m = re.match(r"\[(CP3-[A-Z]\d+)\]", name)
    ok = bool(cond)
    if m:
        RESULTS.setdefault(m.group(1), []).append((ok, name, str(detail)[:300]))
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {str(detail)[:400]}")


# ------------------------------------------------------------------------------------------- small file helpers

def out(r):
    return (r.stdout or "") + (r.stderr or "")


def rb(p):
    try:
        return Path(p).read_bytes()
    except OSError:
        return None


def rt(p):
    b = rb(p)
    return None if b is None else b.decode("utf-8", errors="replace")


def tree(root, skip_top=(".git",)):
    """{relative path: bytes of a file, or None for a folder} for everything under root (top-level .git skipped)."""
    root = str(root)
    res = {}
    for dp, dns, fns in os.walk(root):
        if dp == root:
            dns[:] = [d for d in dns if d not in skip_top]
        for n in dns:
            res[os.path.relpath(os.path.join(dp, n), root)] = None
        for n in fns:
            full = os.path.join(dp, n)
            try:
                with open(full, "rb") as f:
                    res[os.path.relpath(full, root)] = f.read()
            except OSError:
                res[os.path.relpath(full, root)] = b"<unreadable>"
    return res


def new_entries(before, after):
    return sorted(set(after) - set(before))


def differences(before, after):
    """Paths that were removed, added or changed between two tree() snapshots."""
    return sorted(set(before) ^ set(after)) + sorted(k for k in before if k in after and before[k] != after[k])


def files_only(t):
    return {k: v for k, v in t.items() if v is not None}


def find_named(root, name):
    found = []
    for dp, dns, fns in os.walk(str(root)):
        if dp == str(root):
            dns[:] = [d for d in dns if d != ".git"]
        found += [os.path.relpath(os.path.join(dp, n), str(root)) for n in fns if n == name]
    return sorted(found)


_STR_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def has_engine_literal(text, path):
    """True when some JSON string literal in the text parses back to exactly `path`."""
    for m in _STR_RE.finditer(text or ""):
        try:
            if json.loads(m.group(0)) == path:
                return True
        except ValueError:
            pass
    return False


def host_line(text, host):
    m = re.search(r"^[ \t]+" + re.escape(host) + r"\b(.*)$", text or "", re.M)
    return None if m is None else m.group(1).strip()


# -------------------------------------------------------------------------------------- the throwaway project

def git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), check=True, capture_output=True)


class World:
    """A throwaway git project, a throwaway HOME and a throwaway RATCHET_HOME (also the shared-copy folder that
    install.sh writes to), plus the ways to run Ratchet in it with an ambient-proof environment."""

    def __init__(self, full=False):
        self.proj = Path(_tmp.mkdtemp(prefix="ratchet-ocinst-p-")).resolve()
        self.home = Path(_tmp.mkdtemp(prefix="ratchet-ocinst-h-")).resolve()
        self.rh = Path(_tmp.mkdtemp(prefix="ratchet-ocinst-r-")).resolve()
        self.plugin = self.proj / ".opencode" / "plugins" / "ratchet.js"
        self.user_plugin = self.home / ".config" / "opencode" / "plugins" / "ratchet.js"
        git(self.proj, "init", "-q")
        if full:
            git(self.proj, "config", "user.email", "t@t")
            git(self.proj, "config", "user.name", "t")
            (self.proj / "a.js").write_text("x")
            git(self.proj, "add", "-A")
            git(self.proj, "commit", "-qm", "init")

    def env(self, xdg=None, occonf=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(_STRIP_PREFIXES)}
        env.update(HOME=str(self.home), RATCHET_HOME=str(self.rh), PYTHONDONTWRITEBYTECODE="1")
        if xdg is not None:
            env["XDG_CONFIG_HOME"] = str(xdg)
        if occonf is not None:
            env["OPENCODE_CONFIG_DIR"] = str(occonf)
        return env

    def sh(self, *args, stdin=None, engine=None, xdg=None, occonf=None):
        return subprocess.run([sys.executable, str(engine or ENGINE), *args], cwd=str(self.proj), input=stdin,
                              capture_output=True, text=True, env=self.env(xdg, occonf), timeout=180)

    def install_sh(self, *args):
        return subprocess.run(["bash", str(INSTALL_SH), *args], cwd=str(self.proj), capture_output=True, text=True,
                              env=self.env(), timeout=300)

    def install(self, *extra):
        return self.sh("install-hooks", "--host", "opencode", *extra)

    def uninstall(self, *extra):
        return self.sh("install-hooks", "--host", "opencode", "--uninstall", *extra)


def write_user_file(path, content=USER_CODE):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(content)


# ------------------------------------------------------------------------------- checkers (each one has teeth below)

def parse_reply(stdout):
    try:
        obj = json.loads(stdout)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def reply_ok(rc, stdout, stderr, block=False, strict_stderr=True):
    """One JSON object on stdout with a boolean 'block' equal to `block` and a string 'message'; exit 0."""
    obj = parse_reply(stdout)
    return (rc == 0 and (stderr == "" or not strict_stderr) and obj is not None
            and isinstance(obj.get("block"), bool) and obj["block"] is block and isinstance(obj.get("message"), str))


def usage_error_ok(r):
    """An ordinary usage error: non-zero exit, a message on stderr, no traceback, and stdout is never a block reply."""
    obj = parse_reply(r.stdout)
    return (r.returncode != 0 and r.stderr.strip() != "" and "Traceback" not in r.stderr
            and not (obj is not None and obj.get("block") is True))


PLACEHOLDER_RE = re.compile(r"\{\{[^}\n]*\}\}|__[A-Z][A-Z0-9_]{2,}__|@@[A-Za-z_]+@@|%[A-Z][A-Z_]{2,}%|<<[A-Z_]+>>"
                            r"|\bPLACEHOLDER\b")
EXPORT_RE = re.compile(r"export\s+(?:(?:const|let|var|async\s+function|function)\s+RatchetPlugin\b"
                       r"|\{[^}]*\bRatchetPlugin\b[^}]*\})")
V2_RE = re.compile(r"v2.{0,160}?(?:not\s+support|unsupported)|(?:not\s+support|unsupported).{0,160}?v2",
                   re.I | re.S)


def header_problems(text):
    """What is wrong with a rendered plugin's header and shape ([] when it is right)."""
    if text is None:
        return ["file missing"]
    head = text.split("export ", 1)[0]
    flat = re.sub(r"\s*(?://|/\*+|\*+/|\*)\s*", " ", head)
    probs = []
    for need, label in (("1.18.27", "installed version 1.18.27"), ("v1.19.5", "source tag v1.19.5"),
                        ("https://opencode.ai/docs/plugins/", "plugins docs link"),
                        ("https://opencode.ai/docs/sdk/", "sdk docs link")):
        if need not in head:
            probs.append(label)
    if not V2_RE.search(flat):
        probs.append("no statement that the v2 plugin API is not supported")
    if not EXPORT_RE.search(text):
        probs.append("no exported RatchetPlugin")
    if PLACEHOLDER_RE.search(text):
        probs.append("unreplaced placeholder")
    return probs


def ratchet_counts(path):
    """{event: number of Ratchet hook entries} of a JSON hook config, or None when it is missing or has no hooks."""
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    hooks = cfg.get("hooks") if isinstance(cfg, dict) else None
    if not isinstance(hooks, dict):
        return None
    res = {}
    for ev, entries in hooks.items():
        n = 0
        for e in entries or []:
            inner = e.get("hooks") if isinstance(e, dict) and isinstance(e.get("hooks"), list) else [e]
            n += sum(1 for h in inner if "ratchet.py" in json.dumps(h))
        res[ev] = n
    return res


PROJECT_FILES = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "gemini": ".gemini/settings.json",
                 "copilot": ".github/hooks/ratchet.json"}
USER_FILES = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "gemini": ".gemini/settings.json",
              "copilot": ".copilot/hooks/ratchet.json"}
OLD_HOSTS = ("claude", "codex", "gemini", "copilot")


def three_each(counts):
    return counts is not None and sorted(counts.values()) == [1, 1, 1]


def oc_payload(w, event, **kw):
    p = {"hook_event_name": event, "session_id": "ses_1", "cwd": str(w.proj), "tool_name": "bash",
         "tool_input": {"command": "ls"}, "call_id": "call_1", "prompt": "hello", "agent": "build",
         "ratchet_plugin_turn": False}
    p.update(kw)
    return p


OC_EVENTS = (("guard", "tool.execute.before"), ("prompt", "chat.message"), ("stop", "session.idle"))


def active_world(cid):
    """A git project with an active run whose plan was submitted and whose approval question is open: a state where
    guard and prompt allow ordinary work and a stop has nothing to block (as in tests/test_hook_codex_contract.py)."""
    w = World(full=True)
    r = w.sh("init", "opencode install", "--slug", "hk")
    check(f"[{cid}] setup: init worked", r.returncode == 0, out(r)[:200])
    run = w.proj / ".ratchet" / "runs" / "hk"
    (run / "app-scout.json").write_text("{}")
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.test.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r1 = w.sh("plan-submit")
    r2 = w.sh("ask", "approve-plan")
    check(f"[{cid}] setup: plan submitted and approval question asked", r1.returncode == 0 and r2.returncode == 0,
          out(r1)[:150] + out(r2)[:150])
    return w


# ---------------------------------------------------------------------------------------------------- the cases

def case_t01():
    print("[CP3-T01] installing for OpenCode puts Ratchet's plugin in the project's plugins folder")
    w = World()
    before_p, before_h = tree(w.proj), tree(w.home)
    r = w.install()
    text = rt(w.plugin)
    check("[CP3-T01] exit 0", r.returncode == 0, out(r)[:300])
    check("[CP3-T01] .opencode/plugins/ratchet.js exists", text is not None)
    check("[CP3-T01] its first line is exactly the Ratchet header", text is not None and text.split("\n", 1)[0] == HEADER,
          None if text is None else text.split("\n", 1)[0])
    check("[CP3-T01] it holds the installing engine's absolute path as a JSON string literal",
          has_engine_literal(text, str(ENGINE.resolve())))
    check("[CP3-T01] the project gained exactly the .opencode folder, plugins and ratchet.js (no other hook files)",
          new_entries(before_p, tree(w.proj)) == [".opencode", ".opencode/plugins", ".opencode/plugins/ratchet.js"],
          new_entries(before_p, tree(w.proj)))
    check("[CP3-T01] nothing was written under HOME", tree(w.home) == before_h, sorted(tree(w.home)))
    print("[CP3-T01] teeth: the literal finder rejects a path that is not a JSON string literal")
    check("[CP3-T01] teeth: a bare path and an unescaped quote are not accepted",
          not has_engine_literal("const E = /a/b/ratchet.py;", "/a/b/ratchet.py")
          and not has_engine_literal('const E = "/a/my "x"/ratchet.py";', '/a/my "x"/ratchet.py')
          and has_engine_literal('const E = "/a/b/ratchet.py";', "/a/b/ratchet.py"))


def case_t02():
    print("[CP3-T02] --scope user puts the plugin in the user's OpenCode plugins folder")
    w = World()
    before_p = tree(w.proj)
    r = w.install("--scope", "user")
    text = rt(w.user_plugin)
    check("[CP3-T02] exit 0", r.returncode == 0, out(r)[:300])
    check("[CP3-T02] $HOME/.config/opencode/plugins/ratchet.js exists with the Ratchet first line",
          text is not None and text.split("\n", 1)[0] == HEADER, None if text is None else text[:80])
    check("[CP3-T02] the project gets no .opencode folder", not (w.proj / ".opencode").exists()
          and tree(w.proj) == before_p, new_entries(before_p, tree(w.proj)))


def case_t03():
    print("[CP3-T03] the user plugins folder follows OpenCode's config-folder rules (see this file's docstring)")
    base = Path(_tmp.mkdtemp(prefix="ratchet-ocinst-cfg-")).resolve()
    w = World()
    xdg = base / "xdg"
    r = w.sh("install-hooks", "--host", "opencode", "--scope", "user", xdg=xdg)
    want = xdg / "opencode" / "plugins" / "ratchet.js"
    check("[CP3-T03] XDG_CONFIG_HOME: exit 0 and the plugin is in $XDG_CONFIG_HOME/opencode/plugins",
          r.returncode == 0 and first_line_is_header(want), out(r)[:300])
    check("[CP3-T03] XDG_CONFIG_HOME: nothing was written under HOME/.config", not (w.home / ".config").exists(),
          sorted(tree(w.home)))
    w2 = World()
    occonf = base / "occonf"
    r = w2.sh("install-hooks", "--host", "opencode", "--scope", "user", occonf=occonf)
    want2 = occonf / "plugins" / "ratchet.js"
    check("[CP3-T03] OPENCODE_CONFIG_DIR: exit 0 and the plugin is in $OPENCODE_CONFIG_DIR/plugins",
          r.returncode == 0 and first_line_is_header(want2), out(r)[:300])
    check("[CP3-T03] OPENCODE_CONFIG_DIR: nothing was written under HOME/.config", not (w2.home / ".config").exists(),
          sorted(tree(w2.home)))
    w3 = World()
    xdg3, occonf3 = base / "xdg3", base / "occonf3"
    r = w3.sh("install-hooks", "--host", "opencode", "--scope", "user", xdg=xdg3, occonf=occonf3)
    check("[CP3-T03] both set: OPENCODE_CONFIG_DIR wins (the explicit choice), and only that folder gets the plugin",
          r.returncode == 0 and first_line_is_header(occonf3 / "plugins" / "ratchet.js")
          and not (xdg3 / "opencode").exists() and not (w3.home / ".config").exists(), out(r)[:300])


def first_line_is_header(path):
    t = rt(path)
    return t is not None and t.split("\n", 1)[0] == HEADER


def case_t04():
    print("[CP3-T04] installing twice changes nothing the second time")
    old = 1000000000  # 2001: a rewrite would show as a newer mtime
    for label, scope, path_of in (("project", [], lambda w: w.plugin), ("user", ["--scope", "user"],
                                                                         lambda w: w.user_plugin)):
        w = World()
        p = path_of(w)
        r1 = w.install(*scope)
        b1 = rb(p)
        if p.exists():
            os.utime(str(p), (old, old))
        r2 = w.install(*scope)
        st = p.stat() if p.exists() else None
        check(f"[CP3-T04] {label}: both runs exit 0", r1.returncode == 0 and r2.returncode == 0,
              out(r1)[:150] + out(r2)[:150])
        check(f"[CP3-T04] {label}: the file's bytes are identical after the second run", b1 is not None
              and rb(p) == b1)
        check(f"[CP3-T04] {label}: the second run did not rewrite the file (mtime untouched)",
              st is not None and st.st_mtime_ns == old * 1000000000, None if st is None else st.st_mtime_ns)
        check(f"[CP3-T04] {label}: the plugins folder still holds exactly one file",
              p.parent.is_dir() and sorted(os.listdir(str(p.parent))) == ["ratchet.js"],
              sorted(os.listdir(str(p.parent))) if p.parent.is_dir() else None)
        check(f"[CP3-T04] {label}: the second run says it was already installed or unchanged, the first does not",
              ALREADY_RE.search(out(r2)) is not None and ALREADY_RE.search(out(r1)) is None,
              f"first={out(r1)[:100]!r} second={out(r2)[:100]!r}")


def case_t05():
    print("[CP3-T05] re-installing over an older Ratchet-written file brings it up to date")
    w = World()
    write_user_file(w.plugin, (HEADER + '\n// stale body from an older Ratchet\nconst ENGINE = "/old/path/ratchet.py";\n')
                    .encode())
    r = w.install()
    ref = World()
    r0 = ref.install()
    text, want = rt(w.plugin), rt(ref.plugin)
    check("[CP3-T05] exit 0", r.returncode == 0 and r0.returncode == 0, out(r)[:200] + out(r0)[:200])
    check("[CP3-T05] the file now equals a freshly rendered plugin for this engine", text is not None
          and text == want, None if text is None else text[:120])
    check("[CP3-T05] the header is still the first line, once, and the old engine path is gone",
          text is not None and text.split("\n", 1)[0] == HEADER and text.count(HEADER) == 1
          and "/old/path" not in text)
    check("[CP3-T05] it holds the current engine path", has_engine_literal(text, str(ENGINE.resolve())))
    check("[CP3-T05] the plugins folder holds only ratchet.js (nothing duplicated)",
          sorted(os.listdir(str(w.plugin.parent))) == ["ratchet.js"], sorted(os.listdir(str(w.plugin.parent))))


def case_t06():
    print("[CP3-T06] a user's own file at that name is never overwritten")
    variants = (("project, own code", [], lambda w: w.plugin, USER_CODE),
                ("project, empty file", [], lambda w: w.plugin, b""),
                ("user scope, own code", ["--scope", "user"], lambda w: w.user_plugin, USER_CODE))
    for label, scope, path_of, content in variants:
        w = World()
        p = path_of(w)
        write_user_file(p, content)
        before = tree(w.proj), tree(w.home)
        r = w.install(*scope)
        o = out(r)
        check(f"[CP3-T06] {label}: non-zero exit", r.returncode != 0, o[:200])
        check(f"[CP3-T06] {label}: the message names the path", str(p) in o, o[:300])
        check(f"[CP3-T06] {label}: the message says the file is not Ratchet's", NOT_RATCHETS_RE.search(o) is not None,
              o[:300])
        check(f"[CP3-T06] {label}: the message says it was left alone", LEFT_ALONE_RE.search(o) is not None, o[:300])
        check(f"[CP3-T06] {label}: the file is byte-for-byte unchanged", rb(p) == content)
        check(f"[CP3-T06] {label}: no backup or sibling file was created, nothing else changed",
              (tree(w.proj), tree(w.home)) == before, differences(before[0], tree(w.proj))
              + differences(before[1], tree(w.home)))


def case_t07():
    print("[CP3-T07] uninstall removes only Ratchet's plugin file")
    w = World()
    r0 = w.install()
    d = w.plugin.parent
    write_user_file(d / "other.js", b"export const Other = async () => ({});\n")
    write_user_file(d / "ratchet.js.bak", b"an old copy\n")
    write_user_file(d / "my-plugin" / "index.js", b"export const Mine = 1;\n")
    write_user_file(w.proj / ".opencode" / "opencode.json", b'{"theme": "x"}\n')
    before = tree(w.proj)
    check("[CP3-T07] setup: Ratchet's ratchet.js is installed",
          r0.returncode == 0 and ".opencode/plugins/ratchet.js" in before, out(r0)[:200])
    r = w.uninstall()
    after = tree(w.proj)
    want = {k: v for k, v in before.items() if k != ".opencode/plugins/ratchet.js"}
    check("[CP3-T07] exit 0", r.returncode == 0, out(r)[:300])
    check("[CP3-T07] ratchet.js is gone", not w.plugin.exists())
    check("[CP3-T07] every other file and folder is unchanged (other.js, ratchet.js.bak, my-plugin/, opencode.json)",
          ".opencode/plugins/ratchet.js" in before and after == want, differences(want, after))


def case_t08():
    print("[CP3-T08] uninstall leaves a user's own file at that name alone and says why")
    for label, scope, path_of in (("project", [], lambda w: w.plugin), ("user", ["--scope", "user"],
                                                                         lambda w: w.user_plugin)):
        w = World()
        p = path_of(w)
        write_user_file(p)
        r = w.uninstall(*scope)
        o = out(r)
        check(f"[CP3-T08] {label}: exit 0", r.returncode == 0, o[:200])
        check(f"[CP3-T08] {label}: the file still exists with identical bytes", rb(p) == USER_CODE)
        check(f"[CP3-T08] {label}: the output names the path", str(p) in o, o[:300])
        check(f"[CP3-T08] {label}: it says the file is not Ratchet's", NOT_RATCHETS_RE.search(o) is not None, o[:300])
        check(f"[CP3-T08] {label}: it says the file was not removed", LEFT_ALONE_RE.search(o) is not None, o[:300])


def case_t09():
    print("[CP3-T09] uninstall with nothing installed is quiet and creates nothing")
    w = World()
    before_p, before_h = tree(w.proj), tree(w.home)
    r1 = w.uninstall()
    r2 = w.uninstall("--scope", "user")
    check("[CP3-T09] project and user uninstall both exit 0", r1.returncode == 0 and r2.returncode == 0,
          out(r1)[:150] + out(r2)[:150])
    check("[CP3-T09] no .opencode folder was created in the project", not (w.proj / ".opencode").exists()
          and tree(w.proj) == before_p, new_entries(before_p, tree(w.proj)))
    check("[CP3-T09] no ~/.config/opencode folder was created (HOME is still empty)",
          not (w.home / ".config" / "opencode").exists() and tree(w.home) == before_h, sorted(tree(w.home)))
    check("[CP3-T09] no traceback", "Traceback" not in out(r1) + out(r2), (out(r1) + out(r2))[:300])


def case_t10():
    print("[CP3-T10] --dry-run prints the plugin and writes nothing")
    w = World()
    before_p, before_h = tree(w.proj), tree(w.home)
    r = w.install("--dry-run")
    check("[CP3-T10] exit 0", r.returncode == 0, out(r)[:200])
    check("[CP3-T10] stdout holds the Ratchet header line", HEADER in r.stdout.splitlines(), r.stdout[:200])
    check("[CP3-T10] stdout holds the engine path as a JSON string literal",
          has_engine_literal(r.stdout, str(ENGINE.resolve())))
    check("[CP3-T10] no file or folder was created in the project or HOME",
          tree(w.proj) == before_p and tree(w.home) == before_h, new_entries(before_p, tree(w.proj)))
    r1 = w.install()
    b1 = rb(w.plugin)
    r2 = w.uninstall("--dry-run")
    check("[CP3-T10] setup: a real install wrote the plugin", r1.returncode == 0 and b1 is not None, out(r1)[:200])
    check("[CP3-T10] `--uninstall --dry-run` exits 0 and leaves ratchet.js in place, unchanged",
          r2.returncode == 0 and rb(w.plugin) == b1 and b1 is not None, out(r2)[:200])


def case_t11():
    print("[CP3-T11] the installed plugin names the OpenCode version it was written for and its sources")
    w = World()
    w.install()
    text = rt(w.plugin)
    probs = header_problems(text)
    check("[CP3-T11] header names 1.18.27 and v1.19.5, both docs links, the v2 limit; exports RatchetPlugin; no "
          "unreplaced placeholder", probs == [], probs)
    good = (HEADER + "\n// Built for OpenCode 1.18.27 (installed), source v1.19.5.\n// Docs https://opencode.ai/docs/plugins/ "
            "and https://opencode.ai/docs/sdk/.\n// The dev-branch v2 plugin API is not supported.\n"
            "export const RatchetPlugin = async () => ({});\n")
    check("[CP3-T11] teeth: a good sample passes the checker", header_problems(good) == [], header_problems(good))
    check("[CP3-T11] teeth: a sample without v1.19.5 is rejected", header_problems(good.replace("v1.19.5", "v1.19")) != [])
    check("[CP3-T11] teeth: a sample without the v2 statement is rejected",
          header_problems(good.replace("is not supported", "is fine")) != [])
    check("[CP3-T11] teeth: a sample without the export is rejected",
          header_problems(good.replace("export const RatchetPlugin", "const RatchetPlugin")) != [])
    check("[CP3-T11] teeth: a sample with a template placeholder is rejected",
          header_problems(good + 'const E = {{ENGINE_PATH}};\n') != [])


def case_t12():
    print("[CP3-T12] the engine path is written safely when the folder name has spaces or quotes")
    base = Path(_tmp.mkdtemp(prefix="ratchet-ocinst-copy-")).resolve()
    dst = base / 'my skills "x"' / "ratchet-orchestrator"
    shutil.copytree(str(SKILL), str(dst), ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
    engine = dst / "scripts" / "ratchet.py"
    check("[CP3-T12] setup: the copy holds the plugin template next to the engine (assets/opencode-plugin.js)",
          (dst / "assets" / "opencode-plugin.js").is_file())
    w = World()
    r = w.sh("install-hooks", "--host", "opencode", engine=engine)
    text = rt(w.plugin)
    want = str(engine.resolve())
    check("[CP3-T12] exit 0", r.returncode == 0, out(r)[:300])
    check("[CP3-T12] the engine-path literal parses back (as JSON) to exactly the copy's ratchet.py path",
          has_engine_literal(text, want), want)
    check("[CP3-T12] the raw, unescaped path (with its quotes) does not appear in the file",
          text is not None and want not in text)
    check("[CP3-T12] the plugin has the header (the template was found next to the copied engine)",
          first_line_is_header(w.plugin))


def case_t13():
    print("[CP3-T13] doctor lists OpenCode and where its plugin is installed")
    w = World()
    d0 = w.sh("doctor")
    l0 = host_line(d0.stdout, "opencode")
    check("[CP3-T13] nothing installed: doctor exits 0 and its opencode line says 'not installed'",
          d0.returncode == 0 and l0 is not None and "not installed" in l0, f"rc={d0.returncode} line={l0!r}")
    check("[CP3-T13] the list also has claude, codex, gemini and copilot lines",
          all(host_line(d0.stdout, h) is not None for h in OLD_HOSTS), d0.stdout[:300])
    w.install()
    l1 = host_line(w.sh("doctor").stdout, "opencode")
    check("[CP3-T13] after a project install: 'project: <path to .opencode/plugins/ratchet.js>', no user entry",
          l1 is not None and f"project: {w.plugin}" in l1 and "user:" not in l1, l1)
    w.install("--scope", "user")
    l2 = host_line(w.sh("doctor").stdout, "opencode")
    check("[CP3-T13] after the user install too: both the project and the user path are listed",
          l2 is not None and f"project: {w.plugin}" in l2 and f"user: {w.user_plugin}" in l2, l2)
    w.uninstall()
    w.uninstall("--scope", "user")
    l3 = host_line(w.sh("doctor").stdout, "opencode")
    check("[CP3-T13] after both uninstalls it says 'not installed' again", l3 is not None and "not installed" in l3, l3)


def case_t14():
    print("[CP3-T14] doctor does not report a user's own ratchet.js as Ratchet's")
    base = World()
    base_out = base.sh("doctor").stdout
    w = World()
    write_user_file(w.plugin)
    d = w.sh("doctor")
    line = host_line(d.stdout, "opencode")
    check("[CP3-T14] a user-written ratchet.js: the opencode line says 'not installed'",
          d.returncode == 0 and line is not None and "not installed" in line and "project:" not in line,
          f"rc={d.returncode} line={line!r}")
    check("[CP3-T14] the claude, codex, gemini and copilot lines are the same as in a project without that file",
          all(host_line(d.stdout, h) == host_line(base_out, h) and host_line(d.stdout, h) is not None
              for h in OLD_HOSTS), [host_line(d.stdout, h) for h in OLD_HOSTS])
    w2 = World()
    write_user_file(w2.plugin, b"// my notes: ratchet.py is the engine\nexport const Mine = async () => ({});\n")
    line2 = host_line(w2.sh("doctor").stdout, "opencode")
    check("[CP3-T14] a user file that only mentions ratchet.py (no Ratchet header) is not reported either",
          line2 is not None and "not installed" in line2 and "project:" not in line2, line2)


def case_t15():
    print("[CP3-T15] install-hooks --host all still writes exactly the four existing configs and no OpenCode plugin")
    w = World()
    r1 = w.sh("install-hooks", "--host", "all")
    snap1 = (tree(w.proj), tree(w.home))
    r2 = w.sh("install-hooks", "--host", "all")
    snap2 = (tree(w.proj), tree(w.home))
    check("[CP3-T15] both runs exit 0", r1.returncode == 0 and r2.returncode == 0, out(r1)[:150] + out(r2)[:150])
    for h in OLD_HOSTS:
        c = ratchet_counts(w.proj / PROJECT_FILES[h])
        check(f"[CP3-T15] {h}: {PROJECT_FILES[h]} holds three Ratchet hook entries (one per event)", three_each(c), c)
    check("[CP3-T15] no .opencode folder and no ratchet.js plugin file anywhere (project or HOME)",
          not (w.proj / ".opencode").exists() and find_named(w.proj, "ratchet.js") == []
          and find_named(w.home, "ratchet.js") == [] and not (w.home / ".config" / "opencode").exists(),
          find_named(w.proj, "ratchet.js") + find_named(w.home, "ratchet.js"))
    check("[CP3-T15] the second run leaves every file byte-identical", snap1 == snap2,
          differences(snap1[0], snap2[0]) + differences(snap1[1], snap2[1]))


def case_t16():
    print("[CP3-T16] installing OpenCode leaves the other four CLIs' files exactly as they were")
    w = World()
    w.sh("install-hooks", "--host", "all")
    write_user_file(w.proj / ".opencode" / "opencode.json", b'{"theme": "x"}\n')
    before = (files_only(tree(w.proj)), files_only(tree(w.home)))
    r1 = w.install()
    check("[CP3-T16] setup: the OpenCode install wrote the plugin", r1.returncode == 0 and w.plugin.exists(),
          out(r1)[:200])
    r2 = w.uninstall()
    after = (files_only(tree(w.proj)), files_only(tree(w.home)))
    check("[CP3-T16] install then uninstall of OpenCode: every recorded file is byte-identical afterwards",
          r2.returncode == 0 and before == after, differences(before[0], after[0]) + differences(before[1], after[1]))
    w2 = World()
    r3 = w2.install()
    kept = rb(w2.plugin)
    check("[CP3-T16] setup: a project plugin is installed", r3.returncode == 0 and kept is not None, out(r3)[:200])
    for h in OLD_HOSTS:
        w2.sh("install-hooks", "--host", h)
        mid = rb(w2.plugin)
        w2.sh("install-hooks", "--host", h, "--uninstall")
        check(f"[CP3-T16] installing and uninstalling {h} never touches .opencode/plugins/ratchet.js",
              kept is not None and mid == kept and rb(w2.plugin) == kept)


def shared_plugin_text(w, home_based):
    return rt(w.user_plugin if home_based else w.plugin)


def lines_about_opencode_skills(o):
    low = [ln for ln in o.splitlines()]
    false_claim = [ln for ln in low if "config folder was not found" in ln and "opencode" in ln.lower()]
    plain = [ln for ln in low if "opencode" in ln.lower() and "skills" in ln.lower()
             and "config folder was not found" not in ln]
    return false_claim, plain


def case_t17():
    print("[CP3-T17] install.sh --host opencode ships the template and installs the plugin")
    real = Path(os.path.expanduser("~"))
    watched = (real / ".config" / "opencode" / "plugins" / "ratchet.js", real / ".ratchet-loop")

    def sig(p):
        try:
            st = os.lstat(str(p))
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
    sigs = [sig(p) for p in watched]
    w = World()
    r = w.install_sh("--host", "opencode")
    o = out(r)
    shared = w.rh
    template = shared / "skills" / "ratchet-orchestrator" / "assets" / "opencode-plugin.js"
    text = rt(w.user_plugin)
    check("[CP3-T17] exit 0", r.returncode == 0, o[-300:])
    check("[CP3-T17] the template ships: <shared copy>/skills/ratchet-orchestrator/assets/opencode-plugin.js exists",
          template.is_file())
    check("[CP3-T17] $HOME/.config/opencode/plugins/ratchet.js exists with the Ratchet header",
          text is not None and text.split("\n", 1)[0] == HEADER)
    check("[CP3-T17] its engine path is the one inside the shared copy",
          has_engine_literal(text, str(shared / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py")))
    check("[CP3-T17] the real HOME's OpenCode plugin and ~/.ratchet-loop were not touched",
          [sig(p) for p in watched] == sigs, (sigs, [sig(p) for p in watched]))
    false_claim, plain = lines_about_opencode_skills(o)
    check("[CP3-T17] the output never says OpenCode's 'config folder was not found'", false_claim == [], false_claim)
    check("[CP3-T17] the output says plainly what happened to OpenCode's skills (linked, or not linked)",
          plain != [], o[-400:])


def case_t18():
    print("[CP3-T18] install.sh --host opencode --project writes into the repo and is idempotent")
    w = World()
    r1 = w.install_sh("--host", "opencode", "--project")
    b1 = rb(w.plugin)
    r2 = w.install_sh("--host", "opencode", "--project")
    check("[CP3-T18] both runs exit 0", r1.returncode == 0 and r2.returncode == 0, out(r1)[-150:] + out(r2)[-150:])
    check("[CP3-T18] $PWD/.opencode/plugins/ratchet.js exists with the Ratchet header",
          b1 is not None and first_line_is_header(w.plugin))
    check("[CP3-T18] it is byte-identical after the second run", b1 is not None and rb(w.plugin) == b1)
    check("[CP3-T18] nothing was written under HOME/.config/opencode", not (w.home / ".config" / "opencode").exists(),
          sorted(tree(w.home))[:5])


def case_t19():
    print("[CP3-T19] install.sh --host all stays four hosts and the unknown-host help lists opencode")
    w = World()
    r1 = w.install_sh("--host", "all")
    snap1 = (tree(w.proj), tree(w.home))
    r2 = w.install_sh("--host", "all")
    snap2 = (tree(w.proj), tree(w.home))
    check("[CP3-T19] both --host all runs exit 0", r1.returncode == 0 and r2.returncode == 0,
          out(r1)[-150:] + out(r2)[-150:])
    for h in OLD_HOSTS:
        c = ratchet_counts(w.home / USER_FILES[h])
        check(f"[CP3-T19] {h}: ~/{USER_FILES[h]} has one Ratchet entry per event (three events)", three_each(c), c)
    check("[CP3-T19] no OpenCode plugin file or folder under HOME or the project",
          find_named(w.home, "ratchet.js") == [] and find_named(w.proj, "ratchet.js") == []
          and not (w.home / ".config" / "opencode").exists() and not (w.proj / ".opencode").exists(),
          find_named(w.home, "ratchet.js") + find_named(w.proj, "ratchet.js"))
    check("[CP3-T19] the second run changes no hook config (each hook file is byte-identical)",
          all(rb(w.home / USER_FILES[h]) is not None and (snap1[1].get(USER_FILES[h]) == snap2[1].get(USER_FILES[h]))
              for h in OLD_HOSTS))
    rb_ = w.install_sh("--host", "bogus")
    check("[CP3-T19] --host bogus exits non-zero and its message lists opencode among the valid hosts",
          rb_.returncode != 0 and "opencode" in out(rb_).lower(), f"rc={rb_.returncode} {out(rb_)[:300]!r}")


def case_t20():
    print("[CP3-T20] an OpenCode hook call is answered with one JSON reply that says not to block")
    w = active_world("CP3-T20")
    for event, oc_event in OC_EVENTS:
        r = w.sh("hook", event, "--host", "opencode", stdin=json.dumps(oc_payload(w, oc_event)))
        check(f"[CP3-T20] hook {event} --host opencode: exit 0, one JSON object, block false, string message, "
              f"nothing on stderr", reply_ok(r.returncode, r.stdout, r.stderr, block=False),
              f"rc={r.returncode} out={r.stdout[:120]!r} err={r.stderr[:120]!r}")
    print("[CP3-T20] teeth: the reply checker rejects wrong replies")
    good = '{"block": false, "message": ""}\n'
    check("[CP3-T20] teeth: a good reply passes", reply_ok(0, good, ""))
    check("[CP3-T20] teeth: exit 2 is rejected", not reply_ok(2, good, ""))
    check("[CP3-T20] teeth: empty stdout is rejected", not reply_ok(0, "", ""))
    check("[CP3-T20] teeth: a string 'false' is rejected", not reply_ok(0, '{"block": "false", "message": ""}', ""))
    check("[CP3-T20] teeth: a missing message is rejected", not reply_ok(0, '{"block": false}', ""))
    check("[CP3-T20] teeth: two objects are rejected", not reply_ok(0, good + good, ""))
    check("[CP3-T20] teeth: a block reply is rejected when no block is expected",
          not reply_ok(0, '{"block": true, "message": "x"}', ""))
    check("[CP3-T20] teeth: text on stderr is rejected", not reply_ok(0, good, "oops"))


def case_t21():
    print("[CP3-T21] a crash or bad input still answers 'do not block' in the OpenCode shape")
    w = active_world("CP3-T21")
    Path(w.proj / ".ratchet" / "runs" / "hk" / "state.json").write_text("{not json")
    r = w.sh("hook", "guard", "--host", "opencode", stdin=json.dumps(oc_payload(w, "tool.execute.before")))
    check("[CP3-T21] corrupt state.json: exit 0 and one JSON reply with block false",
          reply_ok(r.returncode, r.stdout, r.stderr, block=False, strict_stderr=False) and "Traceback" not in out(r),
          f"rc={r.returncode} out={r.stdout[:120]!r} err={r.stderr[:160]!r}")
    # A guard on a plain `ls` may never read the state file, so it may have nothing to log; a stop always reads it
    # (tests/test_hook_codex_contract.py CP1-X03). Run one too, so the log line (host named opencode) is certain.
    rs = w.sh("hook", "stop", "--host", "opencode", stdin=json.dumps(oc_payload(w, "session.idle")))
    obj = parse_reply(rs.stdout)
    check("[CP3-T21] corrupt state.json, stop: exit 0 and one well-formed JSON reply (boolean block, string message)",
          rs.returncode == 0 and obj is not None and isinstance(obj.get("block"), bool)
          and isinstance(obj.get("message"), str) and "Traceback" not in out(rs),
          f"rc={rs.returncode} out={rs.stdout[:120]!r} err={rs.stderr[:160]!r}")
    log = rt(w.proj / ".ratchet" / "hook-errors.log") or ""
    check("[CP3-T21] corrupt state.json: the error is logged in .ratchet/hook-errors.log under host opencode",
          "opencode guard" in log or "opencode stop" in log, log[-200:])
    for label, stdin in (("empty stdin", ""), ("stdin 'not json'", "not json"), ("a bare JSON list", "[1, 2, 3]")):
        r = w.sh("hook", "guard", "--host", "opencode", stdin=stdin)
        check(f"[CP3-T21] {label}: exit 0 and one JSON reply with block false, no traceback",
              reply_ok(r.returncode, r.stdout, r.stderr, block=False, strict_stderr=False)
              and "Traceback" not in out(r), f"rc={r.returncode} out={r.stdout[:120]!r} err={r.stderr[:160]!r}")


def case_t22():
    print("[CP3-T22] a wrong command line can never be read as a block")
    w = World()
    r1 = w.sh("hook", "nonsense", "--host", "opencode", stdin="{}")
    r2 = w.sh("hook", "guard", "--host", "opencodee", stdin=json.dumps(oc_payload(w, "tool.execute.before")))
    check("[CP3-T22] `hook nonsense --host opencode` is an ordinary usage error (non-zero, no block reply)",
          usage_error_ok(r1), f"rc={r1.returncode} out={r1.stdout[:100]!r} err={r1.stderr[:120]!r}")
    check("[CP3-T22] `hook guard --host opencodee` is an ordinary usage error (non-zero, no block reply)",
          usage_error_ok(r2), f"rc={r2.returncode} out={r2.stdout[:100]!r} err={r2.stderr[:120]!r}")
    print("[CP3-T22] teeth: the usage-error checker rejects a block reply and a clean exit")

    class R:
        def __init__(self, rc, so, se):
            self.returncode, self.stdout, self.stderr = rc, so, se
    check("[CP3-T22] teeth: exit 0 is rejected", not usage_error_ok(R(0, "", "usage: x")))
    check("[CP3-T22] teeth: exit 2 with a block reply on stdout is rejected",
          not usage_error_ok(R(2, '{"block": true, "message": "x"}', "usage: x")))
    check("[CP3-T22] teeth: a traceback is rejected", not usage_error_ok(R(1, "", "Traceback (most recent call last)")))
    check("[CP3-T22] teeth: a plain usage error is accepted", usage_error_ok(R(2, "", "usage: ratchet.py ...")))


# ------------------------------------------------------------------------------------ README (T23)

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
    """The first markdown table in the text (consecutive lines that start with '|'), as text."""
    kept, started = [], False
    for ln in text.splitlines():
        if ln.strip().startswith("|"):
            started = True
            kept.append(ln)
        elif started:
            break
    return "\n".join(kept)


def cli_section(text):
    """The text under the first heading that starts with 'Using it with' (to the next heading of the same or a higher
    level); headings inside code fences are ignored."""
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


def render_cells(name, f):
    """The README cells for one CLI from its HOST_ENFORCEMENT facts (the sentences tests/test_docs.py pins)."""
    records = ("Yes, Ratchet's state and evidence files are protected from hand-edits." if f["records"] else
               "No, Ratchet's state and evidence files are not protected from hand-edits.")
    identity = (f"Yes, {name} tells Ratchet which sub-agent is acting, so Ratchet can confirm reviewer identity."
                if f["identity"] else
                "No, Ratchet has no way to know which sub-agent (reviewer) is at work, so it cannot confirm reviewer "
                "identity.")
    stop = ("Yes, the stop hook stops the agent from ending its turn early." if f["stop"] else
            "No, the stop hook cannot stop the agent from ending its turn early.")
    if not f["replies"]:
        replies = "No, the prompt hook does not record your replies."
    elif f["guidance"]:
        replies = "Yes, the prompt hook records your replies and shows Ratchet's guidance back to the agent."
    else:
        replies = (f"{name}'s prompt hook drops its own message, so the agent never sees Ratchet's guidance, but the "
                   "reply is still recorded in Ratchet's log.")
    return [name, records, identity, stop, replies]


OLD_ROWS = {  # the four existing rows, as they were before OpenCode (HOST_ENFORCEMENT at the start of this run)
    "Claude Code": dict(records=True, identity=True, stop=True, replies=True, guidance=True),
    "Codex": dict(records=True, identity=False, stop=True, replies=True, guidance=True),
    "Gemini": dict(records=True, identity=False, stop=True, replies=True, guidance=True),
    "Copilot": dict(records=True, identity=False, stop=True, replies=True, guidance=False),
}


def row_agrees(cells, enf):
    """An OpenCode row's four cells agree with its HOST_ENFORCEMENT facts: a False fact starts with 'No', a True fact
    does not; reviewer identity is never claimed."""
    if len(cells) != 5 or not isinstance(enf, dict):
        return False
    for cell, key in zip(cells[1:], ("records", "identity", "stop", "replies")):
        if not isinstance(enf.get(key), bool) or not cell:
            return False
        if cell.startswith("No") == bool(enf[key]):
            return False
    return enf["identity"] is False


def host_enforcement():
    """HOST_ENFORCEMENT, read by running the engine file in a child process (never loaded into this process)."""
    code = ("import json, os, runpy, sys\nsys.path.insert(0, os.path.dirname(sys.argv[1]))\n"
            "g = runpy.run_path(sys.argv[1])\nprint(json.dumps(g['HOST_ENFORCEMENT']))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(_STRIP_PREFIXES)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with _tmp.mkdtemp(prefix="ratchet-ocinst-probe-") as cwd:
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


def case_t23():
    print("[CP3-T23] the README table has an honest OpenCode row and the new test is listed")
    sect = cli_section(README.read_text(encoding="utf-8"))
    rows = md_table_rows(first_table(sect))
    data_rows = rows[1:] if len(rows) >= 2 else []
    by_name = {r[0]: r for r in data_rows if r}
    check("[CP3-T23] setup: the per-CLI table was found under 'Using it with ...'", len(rows) >= 2, sect[:120])
    enf = host_enforcement()
    check("[CP3-T23] setup: HOST_ENFORCEMENT could be read from the engine and has an opencode entry",
          isinstance(enf, dict) and isinstance(enf.get("opencode"), dict), enf)
    oc_rows = [r for r in data_rows if r and re.match(r"open\s*code", r[0], re.I)]
    check("[CP3-T23] the table has exactly one OpenCode row with five cells", len(oc_rows) == 1
          and len(oc_rows[0]) == 5, [r[0] for r in data_rows])
    check("[CP3-T23] the table has five data rows (the four existing CLIs and OpenCode)", len(data_rows) == 5,
          [r[0] for r in data_rows])
    oc_enf = enf.get("opencode") if isinstance(enf, dict) else None
    check("[CP3-T23] the OpenCode row's four cells agree with HOST_ENFORCEMENT['opencode'] (No for False, never Yes "
          "for False; reviewer identity never claimed)", len(oc_rows) == 1 and row_agrees(oc_rows[0], oc_enf),
          (oc_rows, oc_enf))
    for name, facts in OLD_ROWS.items():
        check(f"[CP3-T23] the {name} row is unchanged", by_name.get(name) == render_cells(name, facts),
              by_name.get(name))
    print("[CP3-T23] teeth: the row checker rejects a row that claims what HOST_ENFORCEMENT does not")
    none_true = dict(records=False, identity=False, stop=False, replies=False, guidance=False)
    all_no = render_cells("OpenCode", none_true)
    check("[CP3-T23] teeth: an all-No row agrees with an all-False entry", row_agrees(all_no, none_true))
    lying = list(all_no)
    lying[1] = "Yes, Ratchet's state and evidence files are protected from hand-edits."
    check("[CP3-T23] teeth: a Yes cell for a False fact is rejected", not row_agrees(lying, none_true))
    check("[CP3-T23] teeth: a No cell for a True fact is rejected",
          not row_agrees(all_no, dict(none_true, records=True)))
    check("[CP3-T23] teeth: claiming reviewer identity is rejected",
          not row_agrees(render_cells("OpenCode", dict(none_true, identity=True)), dict(none_true, identity=True)))

    block = readme_test_block()
    lst = [ln for ln in block if ln.startswith("python3 tests/")]
    check("[CP3-T23] the 'Running the tests' list holds the exact line exactly once, and it appears nowhere else",
          block.count(NEW_TEST_LINE) == 1 and README.read_text(encoding="utf-8").splitlines().count(NEW_TEST_LINE) == 1,
          block.count(NEW_TEST_LINE))
    if NEW_TEST_LINE in lst:
        i = lst.index(NEW_TEST_LINE)
        ctx = lst[max(0, i - 1):i + 2]
        check("[CP3-T23] its previous neighbour in the list sorts before it", i == 0 or lst[i - 1] < NEW_TEST_LINE, ctx)
        check("[CP3-T23] its next neighbour in the list sorts after it", i + 1 >= len(lst) or lst[i + 1] > NEW_TEST_LINE,
              ctx)
        without = [x for x in lst if x != NEW_TEST_LINE]
        check("[CP3-T23] adding the line adds no out-of-order pair to the list",
              inversions(lst) == inversions(without), inversions(lst))
    else:
        for n in ("its previous neighbour in the list sorts before it", "its next neighbour in the list sorts after it",
                  "adding the line adds no out-of-order pair to the list"):
            check(f"[CP3-T23] {n}", False, "line missing")
    text = THIS_FILE.read_text(encoding="utf-8")
    check("[CP3-T23] this file holds none of the strings the test inventory looks for, so it needs no inventory "
          "entry", inventory_hits(text) == [], inventory_hits(text))
    check("[CP3-T23] teeth: the inventory-string finder does spot them in a sample that loads ratchet.py",
          inventory_hits("importlib.util.spec_from" + "_file_location('x', p)") != [])
    check("[CP3-T23] the file is a tests/test_*.py script that run_all.py picks up",
          THIS_FILE in sorted(p.resolve() for p in (ROOT / "tests").glob("test_*.py") if p.is_file()))


# ------------------------------------------------------------------------------------------------- runner

def guarded(fn, *ids):
    """A case that crashes is reported as an error for its ids (never hides the others)."""
    global FAIL
    try:
        return fn()
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
    for fn, ids in ((case_t01, ("CP3-T01",)), (case_t02, ("CP3-T02",)), (case_t03, ("CP3-T03",)),
                    (case_t04, ("CP3-T04",)), (case_t05, ("CP3-T05",)), (case_t06, ("CP3-T06",)),
                    (case_t07, ("CP3-T07",)), (case_t08, ("CP3-T08",)), (case_t09, ("CP3-T09",)),
                    (case_t10, ("CP3-T10",)), (case_t11, ("CP3-T11",)), (case_t12, ("CP3-T12",)),
                    (case_t13, ("CP3-T13",)), (case_t14, ("CP3-T14",)), (case_t15, ("CP3-T15",)),
                    (case_t16, ("CP3-T16",)), (case_t17, ("CP3-T17",)), (case_t18, ("CP3-T18",)),
                    (case_t19, ("CP3-T19",)), (case_t20, ("CP3-T20",)), (case_t21, ("CP3-T21",)),
                    (case_t22, ("CP3-T22",)), (case_t23, ("CP3-T23",))):
        guarded(fn, *ids)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
