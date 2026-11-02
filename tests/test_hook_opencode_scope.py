#!/usr/bin/env python3
"""
OpenCode support, step 7, round 2 fix: the plugin's missing-engine warning names the right install scope (CP7-X01).
It is its own file because the planned test files are frozen.

    python3 tests/test_hook_opencode_scope.py      # needs node on PATH; temp directories only, no model, no network

The plugin warns at load when its engine file is gone, and the warning ends with the repair command
`install-hooks --host opencode --scope <project|user>`. The scope must be the one the plugin file was installed under,
for EVERY layout install-hooks can write:

  (a) project: <proj>/.opencode/plugins/ratchet.js                                  -> --scope project
  (b) user, default folders: $XDG_CONFIG_HOME/opencode/plugins or ~/.config/opencode/plugins
      (OPENCODE_CONFIG_DIR unset)                                                   -> --scope user
  (c) user, custom OPENCODE_CONFIG_DIR whose own path looks like a project folder
      (OPENCODE_CONFIG_DIR=<home>/.opencode, file <home>/.opencode/plugins/ratchet.js), with and without a trailing
      slash                                                                         -> --scope user   (the round-2 bug)
  (d) project plugin while OPENCODE_CONFIG_DIR points somewhere else                -> --scope project

One structural check (X01) runs the REAL rendered plugin under node with a fake client for each layout, captures the
client.app.log warn line and console.error, and asserts both name the expected scope. A second check (X02) runs
`ratchet.py doctor` on the same layouts and asserts the scope doctor prints is the same one the plugin names, so the
two can never disagree. Doctor can be driven for every layout (it reads the plugin file and the same env).

Teeth: a plugin that drops the scope, one that always says project, one that always says user, and one that looks at the
path pattern before the user folder (the pre-fix behaviour) each fail X01 (X03). If /tmp/k-plugin-before-r2.js exists
(a pre-fix copy of the template), it is run too and must fail.

A fourth check (X04) proves the engine path doctor prints is EXACTLY the path install-hooks wrote into the plugin:
the real engine files are copied under a folder whose name has a space, a backslash, a non-ASCII letter or an emoji, the REAL
`install-hooks --host opencode` is run from that copy (so it writes its own json.dumps literal), the copy is deleted, and
`ratchet.py doctor` must print the original path in its `! opencode plugin (...)` line. Teeth: a decoder that does not
unescape backslashes fails the same comparison.

Each case id appears in its check names as [CP7-X01] ... [CP7-X04].
"""
import json
import os
import re
import shutil
import subprocess
import sys
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "skills" / "ratchet-orchestrator" / "assets" / "opencode-plugin.js"
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
PRE_FIX = Path("/tmp/k-plugin-before-r2.js")
SLOT = "__RATCHET_ENGINE_PATH__"
NODE = shutil.which("node")
PASS = FAIL = 0

DRIVER = r"""
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const scen = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const logs = [], errs = [];
console.error = (...a) => { errs.push(a.map(String).join(' ')); };
const ctx = { directory: scen.dir, worktree: scen.dir,
  client: { app: { log: async (o) => { logs.push(o && o.body); return { data: true }; } } } };
let loaded = false, thrown = null;
try {
  const mod = await import(pathToFileURL(scen.plugin).href);
  await mod.RatchetPlugin(ctx);
  loaded = true;
} catch (e) { thrown = String((e && e.message) || e); }
fs.writeFileSync(scen.out, JSON.stringify({ loaded, thrown, logs, errs }));
process.exit(0);
"""


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f"  -> {detail}" if detail != "" else ""))


def clean_env(home, **extra):
    """An ambient-proof environment: only PATH and a temp HOME; no XDG_*, OPENCODE_* or similar leak in."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LC_ALL": "C.UTF-8"}
    env.update(extra)
    return env


class Layout:
    """One temp world: a project, a home, the plugin file somewhere, an engine path that does not exist."""

    def __init__(self, tmp, label, rel_plugin_dir, expect, cfg=None, cfg_slash=False, xdg=False, base="home"):
        self.label, self.expect = label, expect
        self.home = tmp / label / "home"
        self.proj = tmp / label / "proj"
        self.home.mkdir(parents=True, exist_ok=True)
        self.proj.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=str(self.proj), check=True, capture_output=True, timeout=60)
        root = self.proj if base == "proj" else self.home
        self.plugin_dir = root / rel_plugin_dir
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self.plugin = self.plugin_dir / "ratchet.js"
        self.missing = tmp / label / "no-engine" / "ratchet.py"  # never created
        extra = {}
        if xdg:
            extra["XDG_CONFIG_HOME"] = str(self.home / "xdgconf")
        if cfg is not None:
            extra["OPENCODE_CONFIG_DIR"] = str(cfg(self)) + ("/" if cfg_slash else "")
        self.env = clean_env(self.home, **extra)

    def write(self, template_text):
        assert SLOT in template_text
        self.plugin.write_text(template_text.replace(SLOT, json.dumps(str(self.missing))), encoding="utf-8")

    def run_plugin(self, work):
        scen, out = work / f"{self.label}.json", work / f"{self.label}.out.json"
        scen.write_text(json.dumps({"dir": str(self.proj), "plugin": str(self.plugin), "out": str(out)}), encoding="utf-8")
        p = subprocess.run([NODE, str(work / "driver.mjs"), str(scen)], cwd=str(work), env=self.env,
                           capture_output=True, text=True, timeout=60)
        if not out.exists():
            return {"loaded": False, "thrown": f"no output (rc {p.returncode}): {p.stderr[-300:]}", "logs": [], "errs": []}
        return json.loads(out.read_text(encoding="utf-8"))

    def doctor_scope(self):
        p = subprocess.run([sys.executable, str(ENGINE), "doctor"], cwd=str(self.proj), env=self.env,
                           capture_output=True, text=True, timeout=120)
        hits = re.findall(r"opencode plugin \((\w+)\): engine .* is missing.*--scope (\w+)\s*$", p.stdout, re.M)
        return hits, p.returncode


def layouts(tmp):
    return [
        Layout(tmp, "a-project", ".opencode/plugins", "project", base="proj"),
        Layout(tmp, "b1-user-home", ".config/opencode/plugins", "user"),
        Layout(tmp, "b2-user-xdg", "xdgconf/opencode/plugins", "user", xdg=True),
        Layout(tmp, "c1-user-custom-dotopencode", ".opencode/plugins", "user", cfg=lambda s: s.home / ".opencode"),
        Layout(tmp, "c2-user-custom-trailing-slash", ".opencode/plugins", "user", cfg=lambda s: s.home / ".opencode", cfg_slash=True),
        Layout(tmp, "d-project-with-other-config-dir", ".opencode/plugins", "project", base="proj",
               cfg=lambda s: s.home / "elsewhere"),
    ]


def scope_in(text):
    m = re.search(r"--scope (\w+)", text or "")
    return m.group(1) if m else None


def verdict(lay, res):
    """What the plugin said for one layout: (all-good, details)."""
    warns = [l for l in res["logs"] if isinstance(l, dict) and l.get("level") == "warn"]
    wl = warns[0].get("message", "") if warns else ""
    ce = res["errs"][0] if res["errs"] else ""
    ok = (res["loaded"] and len(warns) == 1 and len(res["errs"]) == 1
          and "engine not found" in wl.lower() and str(lay.missing) in wl and "not running" in wl
          and scope_in(wl) == lay.expect and scope_in(ce) == lay.expect)
    return ok, {"loaded": res["loaded"], "thrown": res["thrown"], "warn": wl, "console": ce, "warns": len(warns)}


def run_all(tmp, template_text, work):
    """Render `template_text` into every layout, run it, return {label: (ok, details)}."""
    out = {}
    for lay in layouts(tmp):
        lay.write(template_text)
        out[lay.label] = verdict(lay, lay.run_plugin(work))
    return out


def mutant(old, new):
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)


X04_CASES = [
    ("a path with spaces", "eng dir with spaces"),
    ("a backslash in the path (a Linux folder name; Windows-style paths cannot be the real engine path here)", "back\\slash\\dir"),
    ("a non-ASCII path (accented letter and CJK)", "caf\u00e9-\u4e16\u754c"),
    ("an astral character in the path (an emoji, written by the installer as a surrogate pair)", "emoji-\U0001F600-dir"),
]


def x04_install(tmp, label, folder):
    """Copy the engine under tmp/<folder>, run its REAL `install-hooks --host opencode` (project scope) and return
    (project, plugin file, the engine path the installer wrote, env). The engine copy is deleted before returning."""
    home, proj = tmp / label / "home", tmp / label / "proj"
    home.mkdir(parents=True)
    proj.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True, capture_output=True, timeout=60)
    skill = tmp / label / folder / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "assets").mkdir()
    for f in ENGINE.parent.glob("*.py"):
        shutil.copy(f, skill / "scripts" / f.name)
    shutil.copy(TEMPLATE, skill / "assets" / TEMPLATE.name)
    eng = skill / "scripts" / "ratchet.py"
    env = clean_env(home)
    p = subprocess.run([sys.executable, str(eng), "install-hooks", "--host", "opencode", "--scope", "project"],
                       cwd=str(proj), env=env, capture_output=True, text=True, timeout=120)
    plugin = proj / ".opencode" / "plugins" / "ratchet.js"
    shutil.rmtree(tmp / label / folder)  # the engine the plugin names is now missing
    return proj, plugin, str(eng.resolve()), env, p


def x04_doctor_path(proj, env, engine=ENGINE):
    p = subprocess.run([sys.executable, str(engine), "doctor"], cwd=str(proj), env=env, capture_output=True, text=True,
                       timeout=120)
    m = re.search(r"^  ! opencode plugin \(project\): engine (.*) is missing, so Ratchet", p.stdout, re.M)
    return (m.group(1) if m else None), p.returncode


def x04_decode_without_unescape(plugin):
    """The mutant decoder: takes the literal between the quotes as is (no unescaping of backslashes)."""
    m = re.search(r'^const RATCHET_ENGINE = "((?:[^"\\]|\\.)*)";', plugin.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def x04_decode_plain_literal_eval(plugin):
    """The mutant decoder: plain ast.literal_eval of the literal, no utf-16 surrogatepass round trip."""
    import ast
    m = re.search(r'^const RATCHET_ENGINE = ("(?:[^"\\]|\\.)*");', plugin.read_text(encoding="utf-8"), re.M)
    return ast.literal_eval(m.group(1)) if m else None


def x04(tmp):
    print("[CP7-X04] doctor prints exactly the engine path install-hooks wrote, for awkward paths")
    for i, (what, folder) in enumerate(X04_CASES):
        proj, plugin, written, env, inst = x04_install(tmp / "x04", f"c{i}", folder)
        check(f"[CP7-X04] {what}: the real installer wrote the plugin", inst.returncode == 0 and plugin.exists(),
              inst.stdout[-200:] + inst.stderr[-200:])
        got, rc = x04_doctor_path(proj, env)
        check(f"[CP7-X04] {what}: doctor exits 0 and prints the exact path the installer wrote", rc == 0 and got == written,
              {"written": written, "doctor": got, "rc": rc})
        if ord(max(folder)) > 0xFFFF:
            bad = x04_decode_plain_literal_eval(plugin)
            check("[CP7-X04] teeth: a decoder that keeps plain ast.literal_eval (no surrogate-pair step) prints a different "
                  "path and is caught", bad is not None and bad != written, {"written": written, "mutant": bad})
        if "\\" in folder:
            bad = x04_decode_without_unescape(plugin)
            check("[CP7-X04] teeth: a decoder that does not unescape backslashes prints a different path and is caught",
                  bad is not None and bad != written, {"written": written, "mutant": bad})


def main():
    if not NODE:
        print("node is not on PATH; this test needs it")
        return 1
    root = Path(_tmp.mkdtemp(prefix="ratchet-scope-"))
    try:
        work = root / "work"
        work.mkdir()
        (work / "driver.mjs").write_text(DRIVER, encoding="utf-8")
        real = TEMPLATE.read_text(encoding="utf-8")

        print("[CP7-X01] the plugin's repair command names the scope it was installed under, in every layout")
        res = run_all(root / "real", real, work)
        for label, (ok, det) in res.items():
            check(f"[CP7-X01] {label}: warn log line and console.error both say the expected scope, with the engine path",
                  ok, det)

        print("[CP7-X02] the scope the plugin names equals the scope `ratchet.py doctor` names, in every layout")
        for lay in layouts(root / "real"):
            hits, rc = lay.doctor_scope()
            plug = scope_in(res[lay.label][1]["warn"])
            check(f"[CP7-X02] {lay.label}: doctor exits 0 and names the same scope as the plugin ({lay.expect})",
                  rc == 0 and len(hits) == 1 and hits[0] == (lay.expect, lay.expect) and plug == lay.expect,
                  {"doctor": hits, "rc": rc, "plugin": plug})

        print("[CP7-X03] teeth: each wrong plugin fails the structural check")
        tail = "Re-run install-hooks --host opencode --scope ${scope}."
        mutants = [
            ("a plugin that drops the scope", mutant(tail, "Re-run install-hooks --host opencode."), None),
            ("a plugin that always says project", mutant(tail, "Re-run install-hooks --host opencode --scope project."), None),
            ("a plugin that always says user", mutant(tail, "Re-run install-hooks --host opencode --scope user."), None),
            ("a plugin that checks the project pattern before the user folder (the pre-fix behaviour)",
             mutant("scope = !inUser && /", "scope = /"), "c1-user-custom-dotopencode"),
        ]
        if PRE_FIX.exists():
            mutants.append(("the saved pre-fix plugin /tmp/k-plugin-before-r2.js", PRE_FIX.read_text(encoding="utf-8"),
                            "c1-user-custom-dotopencode"))
        for i, (name, text, must_fail) in enumerate(mutants):
            r = run_all(root / f"mut{i}", text, work)
            failed = [k for k, (ok, _) in r.items() if not ok]
            check(f"[CP7-X03] {name} is caught" + (f" (and fails {must_fail})" if must_fail else ""),
                  bool(failed) and (must_fail is None or must_fail in failed), {"failed": failed})
        r = run_all(root / "ref", real, work)
        check("[CP7-X03] the real plugin passes every layout again (the reference for the teeth)",
              all(ok for ok, _ in r.values()), [k for k, (ok, _) in r.items() if not ok])

        x04(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
