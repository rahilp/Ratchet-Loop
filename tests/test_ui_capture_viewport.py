#!/usr/bin/env python3
"""
CP1 tests: each capture state can set its own width.   python3 tests/test_ui_capture_viewport.py

A capture spec may give one state its own `viewport` (for example a phone width) next to desktop states. The
state's width beats the spec's width, which beats the 1280x800 default; a state that sets only a width or only a
height takes the other side from the spec (else the default). Each state is captured at its own width, the capture
record names it, and the reviewers' INPUT.md names it per state.

Everything is driven through public entry points, with subprocess only:
  * `node --input-type=module -e` importing skills/ratchet-ui-compare/scripts/capture.mjs and calling its exported
    stateViewport(spec, state) helper (no capture is started by the import);
  * the real `node capture.mjs --spec ... --out ...` run, with PLAYWRIGHT_MODULE pointing at a small stub
    Playwright module this file writes to a temp folder (it logs every newContext viewport and every screenshot),
    so no browser and no network are needed;
  * `python3 skills/ratchet-ui-compare/scripts/ui_review.py stage` on hand-made capture manifests.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402

CAPTURE = ROOT / "skills" / "ratchet-ui-compare" / "scripts" / "capture.mjs"
UI_REVIEW = ROOT / "skills" / "ratchet-ui-compare" / "scripts" / "ui_review.py"
README = README_DOCS
INVENTORY = HERE / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS = Path(__file__).resolve()

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP1-[TX]\d+[a-z]?)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

STUB_PLAYWRIGHT = r"""
import fs from "node:fs";
let ctxN = 0;
const LOG = process.env.STUB_LOG;
const log = (e) => fs.appendFileSync(LOG, JSON.stringify(e) + "\n");
function locator() {
  const l = {
    first: () => l,
    click: async () => {}, fill: async () => {}, hover: async () => {}, selectOption: async () => {},
    check: async () => {}, uncheck: async () => {}, waitFor: async () => {}, scrollIntoViewIfNeeded: async () => {},
    ariaSnapshot: async () => "- body",
    innerText: async () => "stub",
  };
  return l;
}
function page(id) {
  let cur = "about:blank";
  return {
    on() {},
    async goto(u) { cur = u; log({ ev: "goto", ctx: id, url: u }); },
    locator: () => locator(),
    keyboard: { press: async () => {} },
    async waitForLoadState() {}, async evaluate() {}, async addStyleTag() {}, async waitForTimeout() {},
    async screenshot(o) { fs.writeFileSync(o.path, "stub-png"); log({ ev: "shot", ctx: id, path: o.path }); },
    url: () => cur,
    async close() {},
  };
}
const browser = {
  async newContext(opts) {
    const id = ++ctxN;
    log({ ev: "ctx", id, viewport: opts && opts.viewport ? opts.viewport : null });
    return { async newPage() { return page(id); }, async close() {} };
  },
  async newPage() { return page(0); },
  async close() {},
};
export const chromium = { async launch() { return browser; } };
export default { chromium };
"""

_STATE = {}


def workdir():
    if "wd" not in _STATE:
        _STATE["wd"] = _tmp.mkdtemp(prefix="cp1-viewport-")
        stub = Path(_STATE["wd"]) / "stub-playwright.mjs"
        stub.write_text(STUB_PLAYWRIGHT)
        _STATE["stub"] = stub
        _STATE["n"] = 0
    return Path(_STATE["wd"])


def fresh_dir(label):
    wd = workdir()
    _STATE["n"] += 1
    d = wd / f"{label}-{_STATE['n']}"
    d.mkdir()
    return d


def base_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("PLAYWRIGHT_MODULE", "RATCHET_ROLE", "RATCHET_ROOT",
                                                            "RATCHET_HUMAN_OVERRIDE", "STUB_LOG")}
    env["RATCHET_HOME"] = str(workdir() / "home")
    env.update(extra)
    return env


def have_node():
    try:
        return subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


NODE_CALL = r"""
const { pathToFileURL } = await import("node:url");
const mod = await import(pathToFileURL(process.env.CAP_PATH).href);
if (typeof mod.stateViewport !== "function") { console.log(JSON.stringify({ missing: true })); process.exit(0); }
const p = JSON.parse(process.env.CAP_PAYLOAD);
const out = [];
for (const c of p.calls) {
  const spec = p.specs[c.spec];
  const specBefore = JSON.stringify(spec), stateBefore = JSON.stringify(c.state);
  let r = null, err = null;
  try { r = mod.stateViewport(spec, c.state); } catch (e) { err = String(e); }
  out.push({ r, err, specSame: JSON.stringify(spec) === specBefore, stateSame: JSON.stringify(c.state) === stateBefore });
}
console.log(JSON.stringify({ results: out }));
"""

NODE_IMPORT = r"""
const { pathToFileURL } = await import("node:url");
await import(pathToFileURL(process.env.CAP_PATH).href);
"""


def node_import_only(path=CAPTURE, cwd=None):
    return subprocess.run(["node", "--input-type=module", "-e", NODE_IMPORT], cwd=str(cwd or workdir()),
                          capture_output=True, text=True, timeout=60, env=base_env(CAP_PATH=str(path)))


def state_viewports(specs, calls):
    """calls: [(spec_index, state_dict)]. Returns the list of per-call results, or None when the helper is not
    callable (not exported, or importing the script did not stay quiet)."""
    payload = {"specs": specs, "calls": [{"spec": i, "state": s} for i, s in calls]}
    r = subprocess.run(["node", "--input-type=module", "-e", NODE_CALL], cwd=str(workdir()), capture_output=True,
                       text=True, timeout=60, env=base_env(CAP_PATH=str(CAPTURE), CAP_PAYLOAD=json.dumps(payload)))
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, r
    if data.get("missing"):
        return None, r
    return data["results"], r


def vp(w, h):
    return {"width": w, "height": h}


def capture_spec(states, viewport=None):
    spec = {"checkpoint": "CP1", "targets": {"reference": "http://reference.invalid/", "app": "http://app.invalid/"},
            "states": states}
    if viewport is not None:
        spec["viewport"] = viewport
    return spec


def run_capture(spec, label, script=None, cwd=None):
    """Run the real capture.mjs against the stub Playwright. Returns (proc, manifest|None, ctxs, shots)."""
    d = fresh_dir(label)
    spec_path = d / "spec.json"
    spec_path.write_text(json.dumps(spec))
    out = d / "out"
    log = d / "stub.log"
    env = base_env(PLAYWRIGHT_MODULE=str(_STATE["stub"]), STUB_LOG=str(log))
    p = subprocess.run(["node", str(script or CAPTURE), "--spec", str(spec_path), "--out", str(out)],
                       cwd=str(cwd or d), capture_output=True, text=True, timeout=120, env=env)
    manifest = None
    if (out / "manifest.json").is_file():
        try:
            manifest = json.loads((out / "manifest.json").read_text())
        except ValueError:
            manifest = None
    ctxs, shots = {}, []
    if log.is_file():
        for line in log.read_text().splitlines():
            e = json.loads(line)
            if e["ev"] == "ctx":
                ctxs[e["id"]] = e["viewport"]
            elif e["ev"] == "shot":
                shots.append(e)
    return p, manifest, ctxs, shots


def state_target_views(ctxs, shots):
    """{(state_dir_name, target): (set of viewports as (w,h) tuples, number of shots, number of contexts)}."""
    acc = {}
    for s in shots:
        pth = Path(s["path"])
        target = "reference" if pth.name.startswith("reference") else "app"
        ent = acc.setdefault((pth.parent.name, target), {"vps": set(), "shots": 0, "ctx": set()})
        v = ctxs.get(s["ctx"])
        ent["vps"].add((v["width"], v["height"]) if v else None)
        ent["shots"] += 1
        ent["ctx"].add(s["ctx"])
    return acc


def size(v):
    if isinstance(v, dict):
        return (v.get("width"), v.get("height"))
    return None


def tail(p):
    return f"rc={p.returncode} out={p.stdout[-200:]!r} err={p.stderr[-300:]!r}"


# ------------------------------------------------------------------------------------------------ T01, T02, T12

def t01_helper_can_be_asked():
    print("CP1-T01: stateViewport is exported and importing capture.mjs starts no capture")
    check("[CP1-T01] setup: node is on PATH", have_node())
    imp = node_import_only()
    check("[CP1-T01] importing capture.mjs exits 0 (it does not run a capture or exit with usage)",
          imp.returncode == 0, tail(imp))
    check("[CP1-T01] importing capture.mjs prints nothing on stdout or stderr (no usage text)",
          imp.stdout == "" and imp.stderr == "", tail(imp))
    res, raw = state_viewports([{"viewport": vp(1280, 800)}], [(0, {"name": "phone", "viewport": vp(390, 844)})])
    check("[CP1-T01] stateViewport is an exported function that can be called", res is not None, tail(raw))
    if res is not None:
        check("[CP1-T01] stateViewport(spec 1280x800, state 390x844) returns exactly {width:390,height:844}",
              res[0]["err"] is None and res[0]["r"] == vp(390, 844), res[0])


def t02_precedence():
    print("CP1-T02: state width beats spec width beats the 1280x800 default; the spec is never changed")
    specs = [{"viewport": vp(1440, 900)}, {}]
    calls = [(0, {"name": "a", "viewport": vp(390, 844)}),   # (a) state + spec
             (0, {"name": "b"}),                             # (b) no state width, same spec object reused after (a)
             (1, {"name": "c"}),                             # (c) neither
             (1, {"name": "d", "viewport": vp(820, 1180)}),  # (d) state only
             (1, {"name": "e"})]                             # reused empty spec after (d): must not keep 820x1180
    res, raw = state_viewports(specs, calls)
    check("[CP1-T02] setup: stateViewport can be called", res is not None, tail(raw))
    if res is None:
        return
    want = [vp(390, 844), vp(1440, 900), vp(1280, 800), vp(820, 1180), vp(1280, 800)]
    names = ["(a) state 390x844 + spec 1440x900", "(b) no state width + spec 1440x900 (after a)",
             "(c) neither -> 1280x800", "(d) state 820x1180 + no spec width", "(e) neither again (after d)"]
    for i, (w, n) in enumerate(zip(want, names)):
        check(f"[CP1-T02] {n} gives {w['width']}x{w['height']}", res[i]["err"] is None and res[i]["r"] == w, res[i])
    check("[CP1-T02] the spec object is deep-equal to its copy after every call (no width leaks between states)",
          all(r["specSame"] for r in res), [r["specSame"] for r in res])
    check("[CP1-T02] the state object is unchanged after every call", all(r["stateSame"] for r in res))


def t12_partial_widths():
    print("CP1-T12: a state that sets only a width or only a height takes the other side from the spec, else 1280x800")
    specs = [{"viewport": vp(1440, 900)}, {}]
    calls = [(0, {"name": "a", "viewport": {"width": 390}}), (0, {"name": "b", "viewport": {"height": 844}}),
             (1, {"name": "c", "viewport": {"width": 390}}), (1, {"name": "d", "viewport": {"height": 844}})]
    res, raw = state_viewports(specs, calls)
    check("[CP1-T12] setup: stateViewport can be called", res is not None, tail(raw))
    if res is None:
        return
    want = [vp(390, 900), vp(1440, 844), vp(390, 800), vp(1280, 844)]
    names = ["(a) width 390 + spec 1440x900", "(b) height 844 + spec 1440x900", "(c) width 390 + no spec width",
             "(d) height 844 + no spec width"]
    for i, (w, n) in enumerate(zip(want, names)):
        r = res[i]
        check(f"[CP1-T12] {n} gives {w['width']}x{w['height']}, both sides numbers, nothing thrown",
              r["err"] is None and r["r"] == w and all(isinstance(r["r"].get(k), int) for k in ("width", "height")), r)
    check("[CP1-T12] the spec object is unchanged after every call", all(r["specSame"] for r in res))


# ------------------------------------------------------------------------------------------------ T03 - T07, T13

def mixed_run():
    if "mixed" not in _STATE:
        states = [{"name": "list", "scope": "list page"},
                  {"name": "phone", "scope": "phone menu", "viewport": vp(390, 844),
                   "journey": [{"click": "#open"}, {"press": "Escape"}]},
                  {"name": "detail", "scope": "detail page"}]
        _STATE["mixed"] = run_capture(capture_spec(states, vp(1440, 900)), "mixed")
    return _STATE["mixed"]


def t03_mixed_widths_in_one_run():
    print("CP1-T03: one run captures a phone-width state and desktop states each at their own width")
    p, manifest, ctxs, shots = mixed_run()
    check("[CP1-T03] capture exits 0 and writes manifest.json", p.returncode == 0 and manifest is not None, tail(p))
    check("[CP1-T03] the stub saw 6 contexts (reference and app per state)", len(ctxs) == 6, ctxs)
    acc = state_target_views(ctxs, shots)
    want = {"list": (1440, 900), "phone": (390, 844), "detail": (1440, 900)}
    for st, w in want.items():
        for target in ("reference", "app"):
            ent = acc.get((st, target))
            check(f"[CP1-T03] `{st}` {target} is captured from one context of {w[0]}x{w[1]}",
                  ent is not None and ent["vps"] == {w} and len(ent["ctx"]) == 1, ent)
    for target in ("reference", "app"):
        ent = acc.get(("phone", target))
        check(f"[CP1-T03] every phone {target} screenshot (page + 2 journey shots = 3) came from a 390x844 context",
              ent is not None and ent["shots"] == 3 and ent["vps"] == {(390, 844)}, ent)
    got = sorted((v["width"], v["height"]) for v in ctxs.values() if v)
    check("[CP1-T03] the six contexts are 4 at 1440x900 and 2 at 390x844 (the phone width does not carry into `detail`)",
          got == sorted([(1440, 900)] * 4 + [(390, 844)] * 2), got)


def t04_manifest_names_width():
    print("CP1-T04: the capture record names the width each state was captured at")
    p, manifest, ctxs, shots = mixed_run()
    check("[CP1-T04] setup: the mixed capture wrote manifest.json", manifest is not None, tail(p))
    if manifest is None:
        return
    states = {s.get("name"): s for s in manifest.get("states", [])}
    check("[CP1-T04] states are list, phone, detail in order", [s.get("name") for s in manifest.get("states", [])]
          == ["list", "phone", "detail"], list(states))
    for st, w in (("list", vp(1440, 900)), ("phone", vp(390, 844)), ("detail", vp(1440, 900))):
        check(f"[CP1-T04] state `{st}` carries viewport {w['width']}x{w['height']}",
              states.get(st, {}).get("viewport") == w, states.get(st, {}).get("viewport"))
    check("[CP1-T04] the manifest's top-level viewport is still the spec-level 1440x900",
          manifest.get("viewport") == vp(1440, 900), manifest.get("viewport"))
    check("[CP1-T04] existing per-state fields (name, scope, dir, reference, app) are still present",
          all(k in s for s in states.values() for k in ("name", "scope", "dir", "reference", "app")), list(states.values())[:1])


def t05_spec_only_width():
    print("CP1-T05: a spec with only a spec-level width captures every state at that width, as before")
    states = [{"name": "one", "scope": "first"}, {"name": "two", "scope": "second"}]
    p, manifest, ctxs, shots = run_capture(capture_spec(states, vp(1000, 700)), "speconly")
    check("[CP1-T05] capture exits 0 and writes manifest.json", p.returncode == 0 and manifest is not None, tail(p))
    got = sorted((v["width"], v["height"]) for v in ctxs.values() if v)
    check("[CP1-T05] all four contexts open at 1000x700", got == [(1000, 700)] * 4, got)
    if manifest is None:
        return
    check("[CP1-T05] manifest top-level viewport is 1000x700 as before", manifest.get("viewport") == vp(1000, 700),
          manifest.get("viewport"))
    check("[CP1-T05] every manifest state entry also records 1000x700",
          [s.get("viewport") for s in manifest.get("states", [])] == [vp(1000, 700)] * 2,
          [s.get("viewport") for s in manifest.get("states", [])])
    check("[CP1-T05] the manifest keys that existed before are still there",
          all(k in manifest for k in ("checkpoint", "spec_sha256", "viewport", "captured_at", "states")), list(manifest))
    check("[CP1-T05] the console summary lines are unchanged (one ok line per state, then the manifest path)",
          "✓ one: ref=ok app=ok" in p.stdout and "✓ two: ref=ok app=ok" in p.stdout and "manifest: " in p.stdout,
          p.stdout[-300:])


def t06_no_width_anywhere():
    print("CP1-T06: a spec with no width anywhere still captures at 1280x800")
    p, manifest, ctxs, shots = run_capture(capture_spec([{"name": "solo", "scope": "only"}]), "nowidth")
    check("[CP1-T06] capture exits 0 and writes manifest.json", p.returncode == 0 and manifest is not None, tail(p))
    got = sorted((v["width"], v["height"]) for v in ctxs.values() if v)
    check("[CP1-T06] both contexts open at 1280x800", got == [(1280, 800)] * 2, got)
    if manifest is None:
        return
    check("[CP1-T06] manifest top-level viewport is 1280x800", manifest.get("viewport") == vp(1280, 800),
          manifest.get("viewport"))
    check("[CP1-T06] the state entry records 1280x800", [s.get("viewport") for s in manifest.get("states", [])]
          == [vp(1280, 800)], [s.get("viewport") for s in manifest.get("states", [])])


def t07_phone_first_no_spec_width():
    print("CP1-T07: only one state sets a width and the spec sets none: the others stay at 1280x800")
    states = [{"name": "phone", "scope": "phone", "viewport": vp(390, 844)}, {"name": "desktop", "scope": "desktop"}]
    p, manifest, ctxs, shots = run_capture(capture_spec(states), "phonefirst")
    check("[CP1-T07] capture exits 0 and writes manifest.json", p.returncode == 0 and manifest is not None, tail(p))
    acc = state_target_views(ctxs, shots)
    for st, w in (("phone", (390, 844)), ("desktop", (1280, 800))):
        for target in ("reference", "app"):
            ent = acc.get((st, target))
            check(f"[CP1-T07] `{st}` {target} context is {w[0]}x{w[1]}", ent is not None and ent["vps"] == {w}, ent)
    if manifest is None:
        return
    check("[CP1-T07] manifest top-level viewport is the default 1280x800", manifest.get("viewport") == vp(1280, 800),
          manifest.get("viewport"))
    check("[CP1-T07] state entries record 390x844 then 1280x800", [s.get("viewport") for s in manifest.get("states", [])]
          == [vp(390, 844), vp(1280, 800)], [s.get("viewport") for s in manifest.get("states", [])])


def t13_partial_widths_in_a_real_run():
    print("CP1-T13: a partly set state width is captured with the missing side filled in, in a real run")
    states = [{"name": "narrow", "scope": "n", "viewport": {"width": 390}},
              {"name": "short", "scope": "s", "viewport": {"height": 600}},
              {"name": "plain", "scope": "p"}]
    p, manifest, ctxs, shots = run_capture(capture_spec(states, vp(1440, 900)), "partial")
    check("[CP1-T13] capture exits 0 and writes manifest.json", p.returncode == 0 and manifest is not None, tail(p))
    acc = state_target_views(ctxs, shots)
    want = {"narrow": (390, 900), "short": (1440, 600), "plain": (1440, 900)}
    for st, w in want.items():
        for target in ("reference", "app"):
            ent = acc.get((st, target))
            check(f"[CP1-T13] `{st}` {target} context is {w[0]}x{w[1]}", ent is not None and ent["vps"] == {w}, ent)
    if manifest is None:
        return
    recs = [s.get("viewport") for s in manifest.get("states", [])]
    check("[CP1-T13] the manifest state entries record the same full widths and heights, no missing side",
          recs == [vp(390, 900), vp(1440, 600), vp(1440, 900)], recs)


# ------------------------------------------------------------------------------------------------ T08

def t08_import_safe_run_works():
    print("CP1-T08: importing the capture script is safe but running it still works")
    imp = node_import_only()
    check("[CP1-T08] (a) importing with no arguments exits 0 with empty stdout and stderr",
          imp.returncode == 0 and imp.stdout == "" and imp.stderr == "", tail(imp))
    d = fresh_dir("t08")
    link = d / "capture-link.mjs"
    os.symlink(str(CAPTURE), str(link))
    r = subprocess.run(["node", str(CAPTURE)], cwd=str(d), capture_output=True, text=True, timeout=60, env=base_env())
    check("[CP1-T08] (b) `node capture.mjs` with no arguments exits 2 with the usage line",
          r.returncode == 2 and "usage: capture.mjs --spec" in r.stderr, tail(r))
    nospec = d / "spec-no-app.json"
    nospec.write_text(json.dumps({"checkpoint": "CP1", "targets": {"reference": "http://reference.invalid/"},
                                  "states": [{"name": "x"}]}))
    r = subprocess.run(["node", str(link), "--spec", str(nospec), "--out", str(d / "out-c")], cwd=str(d),
                       capture_output=True, text=True, timeout=60, env=base_env())
    check("[CP1-T08] (c) through a symlink, a spec without targets.app exits 2 with `spec.targets.app missing`",
          r.returncode == 2 and "spec.targets.app missing" in r.stderr, tail(r))
    states = [{"name": "home", "scope": "home"}]
    p, manifest, ctxs, shots = run_capture(capture_spec(states, vp(1000, 700)), "t08-link", script=link)
    check("[CP1-T08] (d) a capture started through the symlink runs and writes manifest.json",
          p.returncode == 0 and manifest is not None and len(ctxs) == 2, tail(p))
    rel_dir = fresh_dir("t08-rel")
    rel = os.path.relpath(str(CAPTURE), str(rel_dir))
    p, manifest, ctxs, shots = run_capture(capture_spec(states, vp(1000, 700)), "t08-relpath", script=rel, cwd=rel_dir)
    check("[CP1-T08] (d) a capture started by a relative path runs and writes manifest.json",
          p.returncode == 0 and manifest is not None and len(ctxs) == 2, tail(p))


# ------------------------------------------------------------------------------------------------ T09, T10

def write_manifest(label, viewport, states):
    d = fresh_dir(label)
    cap = d / "cap"
    cap.mkdir()
    (cap / "ref.png").write_bytes(PNG)
    (cap / "app.png").write_bytes(PNG)
    mstates = []
    for name, scope, view in states:
        st = {"name": name, "scope": scope,
              "reference": {"screenshot": str(cap / "ref.png")}, "app": {"screenshot": str(cap / "app.png")}}
        if view is not None:
            st["viewport"] = view
        mstates.append(st)
    m = {"checkpoint": "CP1", "viewport": viewport, "states": mstates}
    mp = cap / "manifest.json"
    mp.write_text(json.dumps(m))
    return d, mp


def stage(d, mp, scope="home"):
    r = subprocess.run([sys.executable, str(UI_REVIEW), "stage", "--manifest", str(mp), "--scope", scope], cwd=str(d),
                       capture_output=True, text=True, timeout=120, env=base_env())
    folders = {}
    try:
        folders = json.loads(r.stdout)
    except ValueError:
        pass
    return r, folders


def input_lines(folder):
    return (Path(folder) / "INPUT.md").read_text().splitlines()


def sections(lines):
    """{heading line: section text} for every `## ` heading up to the next `## ` heading or the end."""
    out, cur = {}, None
    for ln in lines:
        if ln.startswith("## "):
            cur = ln
            out[cur] = [ln]
        elif cur is not None:
            out[cur].append(ln)
    return {k: "\n".join(v) for k, v in out.items()}


def num(n, text):
    return re.search(rf"(?<!\d){n}(?!\d)", text) is not None


def section_for(secs, name):
    for k, v in secs.items():
        if k.startswith(f"## State `{name}`"):
            return v
    return None


def t09_reviewer_input_names_each_width():
    print("CP1-T09: reviewers' input names each state's own width, for both reviewers")
    d, mp = write_manifest("t09", vp(1280, 800), [("home", "home page", vp(1280, 800)),
                                                  ("phone-menu", "phone menu open", vp(390, 844))])
    r, folders = stage(d, mp)
    check("[CP1-T09] setup: stage exits 0 and prints the visual and behavior folders",
          r.returncode == 0 and set(folders) == {"visual", "behavior"}, tail(r))
    for kind in ("visual", "behavior"):
        if kind not in folders:
            check(f"[CP1-T09] {kind} INPUT.md exists", False, tail(r))
            continue
        secs = sections(input_lines(folders[kind]))
        phone, home = section_for(secs, "phone-menu"), section_for(secs, "home")
        check(f"[CP1-T09] {kind}: both state sections exist", phone is not None and home is not None, list(secs))
        if phone is None or home is None:
            continue
        check(f"[CP1-T09] {kind}: the `phone-menu` section names 390 and 844 and not 1280",
              num(390, phone) and num(844, phone) and not num(1280, phone), phone)
        check(f"[CP1-T09] {kind}: the `home` section names 1280 and 800 and not 390",
              num(1280, home) and num(800, home) and not num(390, home), home)
        check(f"[CP1-T09] {kind}: scope text and screenshot lines are still in each section",
              "scope: phone menu open" in phone and "- reference screenshot: phone-menu/reference.png" in phone
              and "- app screenshot: phone-menu/app.png" in phone and "scope: home page" in home
              and "- reference screenshot: home/reference.png" in home and "- app screenshot: home/app.png" in home,
              (phone, home))


def t10_old_manifest_still_staged():
    print("CP1-T10: a capture record from before this change (no per-state width) is still staged")
    top = vp(1000, 700)
    d, mp = write_manifest("t10", top, [("alpha", "alpha scope", None), ("beta", "beta scope", None)])
    r, folders = stage(d, mp, scope="overall")
    check("[CP1-T10] stage exits 0 on the old-shape manifest", r.returncode == 0 and set(folders) == {"visual", "behavior"},
          tail(r))
    for kind in ("visual", "behavior"):
        if kind not in folders:
            check(f"[CP1-T10] {kind} INPUT.md exists", False, tail(r))
            continue
        lines = input_lines(folders[kind])
        old = [("exact", f"# UI review input ({kind})"), ("exact", "Checkpoint: CP1"), ("exact", f"Viewport: {top}"),
               ("exact", "Overall scope — judge ONLY this: overall"),
               ("head", "## State `alpha`", "scope: alpha scope"),
               ("exact", "- reference screenshot: alpha/reference.png"), ("exact", "- app screenshot: alpha/app.png"),
               ("head", "## State `beta`", "scope: beta scope"),
               ("exact", "- reference screenshot: beta/reference.png"), ("exact", "- app screenshot: beta/app.png")]
        pos, missing = 0, None
        for item in old:
            found = None
            for i in range(pos, len(lines)):
                ln = lines[i]
                if (item[0] == "exact" and ln == item[1]) or (item[0] == "head" and ln.startswith(item[1]) and item[2] in ln):
                    found = i
                    break
            if found is None:
                missing = item
                break
            pos = found + 1
        check(f"[CP1-T10] {kind}: every old INPUT.md line is still present, in order", missing is None, missing)
        vline = [ln for ln in lines if ln.startswith("Viewport:")]
        check(f"[CP1-T10] {kind}: the `Viewport:` line still has 1000 and 700",
              len(vline) == 1 and num(1000, vline[0]) and num(700, vline[0]), vline)
        secs = sections(lines)
        for name in ("alpha", "beta"):
            sec = section_for(secs, name)
            check(f"[CP1-T10] {kind}: the `{name}` section names the fallback width 1000 and 700",
                  sec is not None and num(1000, sec) and num(700, sec), sec)


# ------------------------------------------------------------------------------------------------ T11

def t11_readme_and_inventory():
    print("CP1-T11: the test file is listed in the README and the inventory rule is respected")
    readme = README.read_text().splitlines()
    start = next((i for i, ln in enumerate(readme) if ln.strip() == "### Running the tests"), None)
    check("[CP1-T11] setup: README has a 'Running the tests' section", start is not None)
    if start is not None:
        end = next((i for i in range(start + 1, len(readme)) if readme[i].startswith("## ") or readme[i].startswith("### ")),
                   len(readme))
        check("[CP1-T11] the 'Running the tests' list has the exact line `python3 tests/test_ui_capture_viewport.py`",
              "python3 tests/test_ui_capture_viewport.py" in readme[start:end])
    # The two inventory greps from test-inventory.md, built at run time so this file does not match them itself.
    module_load = "spec_from" + "_file_location"
    source_scan = "|".join(["ENGINE" + r"\.read_text", "as" + "t" + r"\.parse", "as" + "t" + r"\.walk", "get" + "source"])
    src = THIS.read_text()
    hits = [p for p in (module_load, source_scan) if re.search(p, src)]
    check("[CP1-T11] this file contains neither inventory pattern (subprocess only), so it needs no inventory entry",
          hits == [], hits)
    inv = INVENTORY.read_text()
    check("[CP1-T11] the inventory rule holds both ways: named in test-inventory.md exactly when a pattern matches",
          (THIS.name in inv) == bool(hits), (THIS.name in inv, hits))
    g = subprocess.run(["grep", "-lE", module_load + "|" + source_scan, str(THIS)], capture_output=True, text=True,
                       timeout=30)
    check("[CP1-T11] the live grep over this file finds nothing", g.returncode == 1 and g.stdout == "", g.stdout)


TESTS = [
    ("CP1-T01", t01_helper_can_be_asked), ("CP1-T02", t02_precedence), ("CP1-T03", t03_mixed_widths_in_one_run),
    ("CP1-T04", t04_manifest_names_width), ("CP1-T05", t05_spec_only_width), ("CP1-T06", t06_no_width_anywhere),
    ("CP1-T07", t07_phone_first_no_spec_width), ("CP1-T08", t08_import_safe_run_works),
    ("CP1-T09", t09_reviewer_input_names_each_width), ("CP1-T10", t10_old_manifest_still_staged),
    ("CP1-T11", t11_readme_and_inventory), ("CP1-T12", t12_partial_widths),
    ("CP1-T13", t13_partial_widths_in_a_real_run),
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
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
