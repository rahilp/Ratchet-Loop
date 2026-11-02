#!/usr/bin/env python3
"""
CP3 tests: scaffold marked stubs for browser cases the capture spec cannot show.   python3 tests/test_ui_capture_stub.py

`ui_review.py lint --spec states/CPn.json --cases tests/CPn.json --scaffold` adds, for each browser case with a gap
(no state, no journey steps, or the wrong width), one stub state named `stub-<case id>` to the capture spec. A stub is
marked `stub: true`, its scope starts with "STUB, UNREVIEWED", it lists the case in `cases`, it carries the case's
`viewport` when the case has one, and it has an empty journey plus empty `reference`/`app` paths to fill in. Existing
states are never altered, a rerun adds nothing, and while the step's capture spec is locked after a failed UI round
(gates.ui.frozen) nothing is written and the message names `ask ui-respec CPn`. capture.mjs keeps the stub mark, the
`cases` list and the width on the manifest entry of each captured state.

Everything is driven through public entry points, with subprocess only:
  * `python3 skills/ratchet-ui-compare/scripts/ui_review.py lint ... [--scaffold]`, in a throwaway project with no
    .ratchet folder and in a throwaway git project with an active Ratchet run (whose UI gate is frozen or not);
  * the real `node capture.mjs --spec ... --out ...` run with PLAYWRIGHT_MODULE pointing at a small stub Playwright
    module this file writes to a temp folder, so no browser and no network are needed.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}. A case that raises is recorded as failed, never dropped.
"""
from __future__ import annotations

import datetime as dt
import hashlib
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

SCRIPTS = ROOT / "skills" / "ratchet-ui-compare" / "scripts"
UI_REVIEW = SCRIPTS / "ui_review.py"
CAPTURE = SCRIPTS / "capture.mjs"
RATCHET_PY = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
README = ROOT / "README.md"
INVENTORY = HERE / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS = Path(__file__).resolve()

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP3-[TX]\d+[a-z]?)\]")


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


# ------------------------------------------------------------------------------------------------ helpers

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

_STATE = {"n": 0}


def workdir():
    if "wd" not in _STATE:
        _STATE["wd"] = _tmp.mkdtemp(prefix="cp3-stub-")
        stub = Path(_STATE["wd"]) / "stub-playwright.mjs"
        stub.write_text(STUB_PLAYWRIGHT)
        _STATE["stub"] = stub
    return Path(_STATE["wd"])


def fresh_dir(label="case"):
    wd = workdir()
    _STATE["n"] += 1
    d = wd / f"{label}-{_STATE['n']}"
    d.mkdir()
    return d


def env(**extra):
    e = {k: v for k, v in os.environ.items()
         if k not in ("RATCHET_ROLE", "RATCHET_ROOT", "RATCHET_HUMAN_OVERRIDE", "PLAYWRIGHT_MODULE", "STUB_LOG")}
    e["RATCHET_HOME"] = str(workdir() / "home")
    e["RATCHET_HOST_PROVIDER"] = "anthropic"
    e["NO_COLOR"] = "1"
    e["PYTHON_COLORS"] = "0"
    e.update(extra)
    return e


def run_ui(args, cwd):
    return subprocess.run([sys.executable, str(UI_REVIEW)] + list(args), capture_output=True, text=True, cwd=str(cwd),
                          env=env(), timeout=180)


def out_of(p):
    return p.stdout + "\n" + p.stderr


def warn_lines(p):
    return [ln for ln in out_of(p).splitlines() if ln.lstrip().startswith("WARN")]


def vp(w, h):
    return {"width": w, "height": h}


JOURNEY = [{"click": "#open"}, {"press": "Escape"}]


def browser(cid, title, **extra):
    c = {"id": cid, "title": title, "layer": "browser"}
    c.update(extra)
    return c


def code(cid, title, **extra):
    c = {"id": cid, "title": title, "layer": "code"}
    c.update(extra)
    return c


def real_state(name, cases, journey=JOURNEY, viewport=None):
    s = {"name": name, "scope": f"The {name} screen", "cases": cases, "journey": journey,
         "reference": {"path": f"/{name}"}, "app": {"path": f"/{name}"}}
    if viewport is not None:
        s["viewport"] = viewport
    return s


T01_TITLE = "Home page lists the items"
T02_TITLE = "Approve button shows a spinner while saving"
T03_TITLE = "Menu collapses into a hamburger on a phone"
T04_TITLE = "Cart shows the running total"
T05_TITLE = "Checkout fits a phone screen"
T07_TITLE = "Receipt page prints the order number"
STUB_IDS = ["CP1-T02", "CP1-T03", "CP1-T04", "CP1-T05"]


def spec_a():
    return {"checkpoint": "CP1", "viewport": vp(1280, 800),
            "targets": {"reference": "http://reference.invalid/", "app": "http://app.invalid/"},
            "login": {"reference": [{"goto": "/login"}], "app": [{"goto": "/login"}]},
            "states": [real_state("home", ["CP1-T01"]),
                       real_state("cart", ["CP1-T04"], journey=[]),
                       real_state("checkout", ["CP1-T05"])]}


def plan_a():
    return {"cases": [browser("CP1-T01", T01_TITLE),
                      browser("CP1-T02", T02_TITLE),
                      browser("CP1-T03", T03_TITLE, viewport=vp(390, 844)),
                      browser("CP1-T04", T04_TITLE),
                      browser("CP1-T05", T05_TITLE, viewport=vp(390, 844)),
                      code("CP1-T06", "A pure code case")]}


def spec_covered():
    return {"checkpoint": "CP1", "viewport": vp(1280, 800),
            "targets": {"reference": "http://reference.invalid/", "app": "http://app.invalid/"},
            "states": [real_state("home", ["CP1-T01"]),
                       real_state("phone", ["CP1-T03"], viewport=vp(390, 844))]}


def plan_covered():
    return {"cases": [browser("CP1-T01", T01_TITLE), browser("CP1-T03", T03_TITLE, viewport=vp(390, 844)),
                      code("CP1-T06", "A pure code case")]}


def write_project(d, spec, plan, spec_rel="states/CP1.json", cases_rel="tests/CP1.json"):
    for rel, obj in ((spec_rel, spec), (cases_rel, plan)):
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj))
    return Path(d) / spec_rel


def project_a(spec=None, plan=None, label="a"):
    d = fresh_dir(label)
    spec_path = write_project(d, spec if spec is not None else spec_a(), plan if plan is not None else plan_a())
    return d, spec_path


def scaffold(d, spec_rel="states/CP1.json", cases_rel="tests/CP1.json", flag=True):
    args = ["lint", "--spec", spec_rel, "--cases", cases_rel]
    if flag:
        args.append("--scaffold")
    return run_ui(args, d)


def load(path):
    return json.loads(Path(path).read_text())


def stubs_of(spec):
    return [s for s in spec.get("states", []) if isinstance(s, dict) and str(s.get("name", "")).startswith("stub-")]


def by_name(spec, name):
    hits = [s for s in spec.get("states", []) if isinstance(s, dict) and s.get("name") == name]
    return hits[0] if len(hits) == 1 else None


def no_traceback(p):
    return "Traceback" not in p.stdout and "Traceback" not in p.stderr


# ---- a throwaway git project with an active Ratchet run (CP1 and CP2 both have a UI gate) ----

def _git(d, *a):
    return subprocess.run(["git", *a], cwd=str(d), capture_output=True, text=True)


def _rx(d, *a, stdin=None):
    return subprocess.run([sys.executable, str(RATCHET_PY), *a], cwd=str(d), capture_output=True, text=True, env=env(),
                          input=stdin, timeout=180)


def build_run_project():
    d = Path(_tmp.mkdtemp(prefix="cp3-run-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        _git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    tp = Path(str(d) + "-transcript.jsonl")

    def say(text):
        with tp.open("a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
        return _rx(d, "hook", "prompt", "--host", "claude",
                   stdin=json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner", "transcript_path": str(tp)}))

    say("please build the feature")
    r = _rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    _rx(d, "config", "--set", "min_review_seconds=0", "providers=[]")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cps = []
    for i in (1, 2):
        cps.append({"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
                    "gates": {"behavior": True, "ui": True, "adversarial": True}, "risk": "low",
                    "ui_scope": "all", "ui_states": [{"name": "empty"}],
                    "tests": {"files": [f"tests/cp{i}.test.js"], "command": "true"}})
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code",
                                                                           "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": cps}))
    r = _rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    _rx(d, "ask", "approve-plan")
    say("yes, go ahead")
    r = _rx(d, "approve-plan")
    assert r.returncode == 0, r.stderr + r.stdout
    (run / "states").mkdir(exist_ok=True)
    return d


FREEZE_CODE = """
import json, sys
sys.path.insert(0, %r)
import ui_review as u
eng = u._engine()
ctx = eng.Ctx(eng.find_root())
st = ctx.state()
frozen = json.loads(sys.argv[1])
for cp, on in frozen.items():
    g = st["checkpoints"][cp]["gates"]["ui"]
    if on:
        g["frozen"] = {"spec_sha256": "0" * 64, "scope": "all"}
    else:
        g.pop("frozen", None)
ctx.save_state(st)
"""


def run_project():
    if "runproj" not in _STATE:
        _STATE["runproj"] = build_run_project()
    return _STATE["runproj"]


def set_frozen(d, **by_cp):
    """Freeze (True) or unfreeze (False) a step's UI gate, through the engine's own save_state (a hand edit of
    state.json is refused as tampering)."""
    r = subprocess.run([sys.executable, "-c", FREEZE_CODE % str(SCRIPTS), json.dumps(by_cp)], cwd=str(d),
                       capture_output=True, text=True, env=env(), timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    for cp, on in by_cp.items():
        assert bool(st["checkpoints"][cp]["gates"]["ui"].get("frozen")) == on, (cp, on)


RUN_SPEC_REL = ".ratchet/runs/f/states/CP1.json"


def run_case(frozen, label="run"):
    """The shared run project, with CP1/CP2 frozen as asked and a fresh spec (Project A's) in the run's states folder."""
    d = run_project()
    set_frozen(d, **frozen)
    write_project(d, spec_a(), plan_a(), spec_rel=RUN_SPEC_REL, cases_rel="tests/CP1.json")
    return d, d / RUN_SPEC_REL


# ---- capture ----

def have_node():
    try:
        return subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_capture(spec_path):
    workdir()
    d = fresh_dir("cap")
    out = d / "out"
    log = d / "stub.log"
    p = subprocess.run(["node", str(CAPTURE), "--spec", str(spec_path), "--out", str(out)], cwd=str(d),
                       capture_output=True, text=True, timeout=180,
                       env=env(PLAYWRIGHT_MODULE=str(_STATE["stub"]), STUB_LOG=str(log)))
    manifest = None
    mp = out / "manifest.json"
    if mp.is_file():
        try:
            manifest = json.loads(mp.read_text())
        except ValueError:
            manifest = None
    return p, manifest


# ------------------------------------------------------------------------------------------------ cases

def t01_one_stub_per_gap():
    print("CP3-T01: scaffold adds exactly one stub per case the capture is missing, named after the case")
    d, sp = project_a()
    before = load(sp)
    p = scaffold(d)
    check("[CP3-T01] scaffold exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
    after = load(sp)
    new = [s.get("name") for s in after.get("states", [])][len(before["states"]):]
    check("[CP3-T01] exactly four new states, stub-CP1-T02..T05 (no T01, no T06)",
          sorted(new) == sorted("stub-" + i for i in STUB_IDS), new)
    check("[CP3-T01] the command prints each stub's name",
          all(("stub-" + i) in p.stdout for i in STUB_IDS), p.stdout[-400:])
    check("[CP3-T01] no stub for the covered case or the code case",
          "stub-CP1-T01" not in out_of(p) and "stub-CP1-T06" not in out_of(p)
          and by_name(after, "stub-CP1-T01") is None and by_name(after, "stub-CP1-T06") is None)


def t02_stub_shape():
    print("CP3-T02: each stub is plainly marked as a stub and unreviewed, and ready to fill in")
    d, sp = project_a()
    scaffold(d)
    st = by_name(load(sp), "stub-CP1-T02")
    check("[CP3-T02] the stub state stub-CP1-T02 exists exactly once", st is not None, [s.get("name") for s in load(sp)["states"]])
    st = st or {}
    check("[CP3-T02] stub is true", st.get("stub") is True, st)
    check("[CP3-T02] cases is exactly [CP1-T02]", st.get("cases") == ["CP1-T02"], st.get("cases"))
    check("[CP3-T02] journey is an empty list", st.get("journey") == [], st.get("journey"))
    for side in ("reference", "app"):
        e = st.get(side)
        check(f"[CP3-T02] {side} is an object with a path key that is empty",
              isinstance(e, dict) and "path" in e and e["path"] == "", e)
    scope = st.get("scope")
    check("[CP3-T02] scope starts with 'STUB, UNREVIEWED'", isinstance(scope, str) and scope.startswith("STUB, UNREVIEWED"), scope)
    s = scope if isinstance(scope, str) else ""
    check("[CP3-T02] scope names CP1-T02 and the case's title", "CP1-T02" in s and T02_TITLE in s, s)
    check("[CP3-T02] scope says to add the journey and then remove the stub mark",
          "journey" in s.lower() and "remove" in s.lower() and "stub" in s.lower().replace("stub, unreviewed", ""), s)


def t03_stub_width():
    print("CP3-T03: a stub takes the case's width when the case has one, and none otherwise")
    d, sp = project_a()
    scaffold(d)
    spec = load(sp)
    s3, s2, s5 = by_name(spec, "stub-CP1-T03"), by_name(spec, "stub-CP1-T02"), by_name(spec, "stub-CP1-T05")
    check("[CP3-T03] stub-CP1-T03 exists and has viewport 390x844",
          s3 is not None and s3.get("viewport") == vp(390, 844), s3)
    check("[CP3-T03] stub-CP1-T02 exists and has no viewport key at all", s2 is not None and "viewport" not in s2, s2)
    check("[CP3-T03] the wrong-width stub-CP1-T05 also carries 390x844",
          s5 is not None and s5.get("viewport") == vp(390, 844), s5)
    s4 = by_name(spec, "stub-CP1-T04")
    check("[CP3-T03] the no-journey stub-CP1-T04 (case without a width) has no viewport key",
          s4 is not None and "viewport" not in s4, s4)


def t04_existing_untouched():
    print("CP3-T04: existing states and spec settings are never altered")
    d, sp = project_a()
    before = load(sp)
    scaffold(d)
    after = load(sp)
    n = len(before["states"])
    check("[CP3-T04] the original states are deeply equal, in the same order, at the start",
          json.dumps(after.get("states", [])[:n]) == json.dumps(before["states"]) and len(after.get("states", [])) > n,
          [s.get("name") for s in after.get("states", [])])
    for k in ("checkpoint", "viewport", "targets", "login"):
        check(f"[CP3-T04] top-level {k} is unchanged", after.get(k) == before.get(k), after.get(k))
    check("[CP3-T04] no other top-level key appears or disappears",
          set(after) == set(before), (sorted(after), sorted(before)))
    check("[CP3-T04] every state after the originals is a stub",
          all(str(s.get("name", "")).startswith("stub-") for s in after.get("states", [])[n:]),
          [s.get("name") for s in after.get("states", [])[n:]])
    check("[CP3-T04] the spec had no per-state width and the originals still have none",
          all("viewport" not in s for s in after.get("states", [])[:n]))


def t05_gap_on_existing_state():
    print("CP3-T05: a gap on an existing state gets its own stub instead of editing that state")
    d, sp = project_a()
    before = load(sp)
    scaffold(d)
    after = load(sp)
    check("[CP3-T05] stub-CP1-T04 and stub-CP1-T05 are separate states",
          by_name(after, "stub-CP1-T04") is not None and by_name(after, "stub-CP1-T05") is not None,
          [s.get("name") for s in after.get("states", [])])
    cart, checkout = by_name(after, "cart"), by_name(after, "checkout")
    check("[CP3-T05] 'cart' still has its empty journey and is unchanged",
          cart is not None and cart.get("journey") == [] and cart == before["states"][1], cart)
    check("[CP3-T05] 'checkout' still has no viewport, keeps its journey and is unchanged",
          checkout is not None and "viewport" not in checkout and checkout.get("journey") == JOURNEY
          and checkout == before["states"][2], checkout)


def t06_idempotent():
    print("CP3-T06: running scaffold again adds nothing")
    d, sp = project_a()
    scaffold(d)
    first = sp.read_bytes()
    for n in (2, 3):
        p = scaffold(d)
        check(f"[CP3-T06] run {n} exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
        check(f"[CP3-T06] run {n} leaves the file byte-for-byte identical", sp.read_bytes() == first)
        check(f"[CP3-T06] run {n} says there is nothing to add", "nothing to add" in out_of(p).lower(), out_of(p)[-300:])
    names = [s.get("name") for s in load(sp).get("states", []) if str(s.get("name", "")).startswith("stub-")]
    check("[CP3-T06] there are exactly four stubs and no duplicate names",
          sorted(names) == sorted("stub-" + i for i in STUB_IDS), names)


def t07_started_stub_kept():
    print("CP3-T07: a stub the author already started filling in is never overwritten or duplicated")
    started = {"name": "stub-CP1-T02", "stub": True, "cases": ["CP1-T02"],
               "scope": "STUB, UNREVIEWED: my own wording for the approve spinner", "journey": JOURNEY,
               "reference": {"path": "/approve"}, "app": {"path": "/approve"}}
    spec = spec_a()
    spec["states"].append(started)
    d, sp = project_a(spec=spec)
    p = scaffold(d)
    after = load(sp)
    names = [s.get("name") for s in after.get("states", [])]
    check("[CP3-T07] scaffold exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
    check("[CP3-T07] exactly one stub-CP1-T02", names.count("stub-CP1-T02") == 1, names)
    check("[CP3-T07] the author's stub is unchanged, key order included",
          json.dumps(by_name(after, "stub-CP1-T02")) == json.dumps(started), by_name(after, "stub-CP1-T02"))
    check("[CP3-T07] the other missing cases still get stubs",
          all(("stub-CP1-T0%d" % i) in names for i in (3, 4, 5)), names)
    # The author only reworded the scope: the journey is still empty, so the case still has a gap, yet the stub stays.
    started2 = dict(started, journey=[], scope="STUB, UNREVIEWED: reworded but no steps yet")
    spec2 = spec_a()
    spec2["states"].append(started2)
    d2, sp2 = project_a(spec=spec2, label="a2")
    p2 = scaffold(d2)
    after2 = load(sp2)
    names2 = [s.get("name") for s in after2.get("states", [])]
    check("[CP3-T07] (empty journey) exits 0", p2.returncode == 0, (p2.returncode, out_of(p2)[-300:]))
    check("[CP3-T07] (empty journey) still exactly one stub-CP1-T02, unchanged",
          names2.count("stub-CP1-T02") == 1 and json.dumps(by_name(after2, "stub-CP1-T02")) == json.dumps(started2),
          by_name(after2, "stub-CP1-T02"))
    check("[CP3-T07] (empty journey) the other missing cases still get stubs",
          all(("stub-CP1-T0%d" % i) in names2 for i in (3, 4, 5)), names2)


def t08_new_case_later():
    print("CP3-T08: a case added to the plan later gets a stub on the next run, only for it")
    d, sp = project_a()
    scaffold(d)
    mid = load(sp)
    plan = plan_a()
    plan["cases"].append(browser("CP1-T07", T07_TITLE))
    (d / "tests/CP1.json").write_text(json.dumps(plan))
    p = scaffold(d)
    after = load(sp)
    check("[CP3-T08] scaffold exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
    n = len(mid["states"])
    new = after.get("states", [])[n:]
    check("[CP3-T08] exactly one new state, stub-CP1-T07, appended at the end",
          [s.get("name") for s in new] == ["stub-CP1-T07"], [s.get("name") for s in new])
    check("[CP3-T08] the earlier states (real and the four stubs) are unchanged",
          json.dumps(after.get("states", [])[:n]) == json.dumps(mid["states"]))
    st = new[0] if new else {}
    check("[CP3-T08] the new stub is marked and names its case",
          st.get("stub") is True and st.get("cases") == ["CP1-T07"] and T07_TITLE in str(st.get("scope")), st)


def t09_locked_refuses():
    print("CP3-T09: while the capture spec is locked after a failed UI round, scaffold writes nothing and says why")
    d, sp = run_case({"CP1": True, "CP2": False})
    before = sp.read_bytes()
    p = scaffold(d, spec_rel=RUN_SPEC_REL)
    o = out_of(p)
    check("[CP3-T09] the spec file is byte-for-byte unchanged", sp.read_bytes() == before)
    check("[CP3-T09] the command exits non-zero (a refusal)", p.returncode != 0, (p.returncode, o[-300:]))
    check("[CP3-T09] the message says the spec is locked after a failed UI round",
          "locked" in o.lower() and "failed ui round" in o.lower(), o[-400:])
    check("[CP3-T09] the message names `ask ui-respec CP1`", "ask ui-respec CP1" in o, o[-400:])
    check("[CP3-T09] no traceback", no_traceback(p), o[-300:])
    check("[CP3-T09] no stub is added", stubs_of(load(sp)) == [], [s.get("name") for s in load(sp)["states"]])


def t10_locked_still_warns():
    print("CP3-T10: a locked spec still gets the warnings, only the writing is refused")
    d, sp = run_case({"CP1": True, "CP2": False})
    before = sp.read_bytes()
    p = scaffold(d, spec_rel=RUN_SPEC_REL)
    w = warn_lines(p)
    check("[CP3-T10] the refusal is real (non-zero) and nothing was written",
          p.returncode != 0 and sp.read_bytes() == before, (p.returncode, out_of(p)[-300:]))
    for cid in STUB_IDS:
        check(f"[CP3-T10] a WARN line names {cid}", any(cid in ln for ln in w), w)
    check("[CP3-T10] no WARN line for the covered CP1-T01 or the code case CP1-T06",
          not any("CP1-T01" in ln or "CP1-T06" in ln for ln in w), w)


def t11_lock_is_per_step():
    print("CP3-T11: another step's lock, or no lock, does not stop scaffolding")
    for label, frozen in (("CP2 frozen only", {"CP1": False, "CP2": True}), ("nothing frozen", {"CP1": False, "CP2": False})):
        d, sp = run_case(frozen)
        before = load(sp)
        p = scaffold(d, spec_rel=RUN_SPEC_REL)
        after = load(sp)
        new = [s.get("name") for s in after.get("states", [])][len(before["states"]):]
        check(f"[CP3-T11] ({label}) exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
        check(f"[CP3-T11] ({label}) the four stubs are written",
              sorted(new) == sorted("stub-" + i for i in STUB_IDS), new)
        check(f"[CP3-T11] ({label}) no locked message", "ui-respec" not in out_of(p), out_of(p)[-300:])


def t12_no_run_no_lock():
    print("CP3-T12: outside any Ratchet run there is no lock")
    d, sp = project_a()
    check("[CP3-T12] setup: the project has no .ratchet folder", not (d / ".ratchet").exists())
    p = scaffold(d)
    check("[CP3-T12] exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
    new = [s.get("name") for s in load(sp).get("states", [])][3:]
    check("[CP3-T12] the stubs are written", sorted(new) == sorted("stub-" + i for i in STUB_IDS), new)
    low = out_of(p).lower()
    check("[CP3-T12] no error about a missing run or state, no traceback",
          no_traceback(p) and "error" not in low and "no active" not in low and "state.json" not in low
          and "ui-respec" not in low, out_of(p)[-400:])
    check("[CP3-T12] scaffold did not create a .ratchet folder", not (d / ".ratchet").exists())


def t13_check_only_never_writes():
    print("CP3-T13: the check without --scaffold never writes the spec")
    d, sp = project_a()
    before = sp.read_bytes()
    p = scaffold(d, flag=False)
    w = warn_lines(p)
    check("[CP3-T13] exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
    check("[CP3-T13] the spec file is byte-for-byte unchanged", sp.read_bytes() == before)
    for cid in STUB_IDS:
        check(f"[CP3-T13] a WARN line names {cid}", any(cid in ln for ln in w), w)
    check("[CP3-T13] four WARN lines in all", len(w) == 4, w)
    check("[CP3-T13] no stub is mentioned as added", "stub-CP1" not in p.stdout, p.stdout[-300:])


def t14_nothing_missing():
    print("CP3-T14: scaffold with nothing missing leaves the spec alone")
    for label, spec, plan in (("all covered", spec_covered(), plan_covered()),
                              ("code cases only", spec_a(), {"cases": [code("CP1-T01", "x"), code("CP1-T02", "y")]})):
        d, sp = project_a(spec=spec, plan=plan, label="ok")
        before = sp.read_bytes()
        p = scaffold(d)
        check(f"[CP3-T14] ({label}) exits 0", p.returncode == 0, (p.returncode, out_of(p)[-300:]))
        check(f"[CP3-T14] ({label}) the spec file is byte-for-byte unchanged", sp.read_bytes() == before)
        check(f"[CP3-T14] ({label}) no stub was added", stubs_of(load(sp)) == [])
        check(f"[CP3-T14] ({label}) no warning is printed", "WARN" not in out_of(p), out_of(p)[-300:])


def stub_state(cid, title, width=None, extra=None):
    s = {"name": f"stub-{cid}", "stub": True, "cases": [cid],
         "scope": f"STUB, UNREVIEWED: add the journey that demonstrates {cid} ({title}), then remove stub",
         "journey": [], "reference": {"path": ""}, "app": {"path": ""}}
    if width is not None:
        s["viewport"] = width
    s.update(extra or {})
    return s


def t15_capture_keeps_stub_mark():
    print("CP3-T15: a captured stub keeps its stub mark, its cases and its width in the capture record")
    check("[CP3-T15] setup: node is on PATH", have_node())
    spec = {"checkpoint": "CP1", "viewport": vp(1280, 800),
            "targets": {"reference": "http://reference.invalid/", "app": "http://app.invalid/"},
            "states": [real_state("home", ["CP1-T01"]), stub_state("CP1-T03", T03_TITLE, vp(390, 844))]}
    d = fresh_dir("cap-in")
    sp = d / "spec.json"
    sp.write_text(json.dumps(spec))
    p, m = run_capture(sp)
    check("[CP3-T15] capture exits 0 and writes a manifest", p.returncode == 0 and isinstance(m, dict),
          (p.returncode, p.stderr[-300:]))
    states = {s.get("name"): s for s in (m or {}).get("states", [])}
    st, home = states.get("stub-CP1-T03", {}), states.get("home", {})
    check("[CP3-T15] the stub entry has stub true", st.get("stub") is True, st)
    check("[CP3-T15] the stub entry has cases [CP1-T03]", st.get("cases") == ["CP1-T03"], st.get("cases"))
    check("[CP3-T15] the stub entry has viewport 390x844", st.get("viewport") == vp(390, 844), st.get("viewport"))
    check("[CP3-T15] the stub entry keeps its 'STUB, UNREVIEWED' scope text",
          str(st.get("scope", "")).startswith("STUB, UNREVIEWED"), st.get("scope"))
    check("[CP3-T15] the home entry has no stub mark (absent or false)", bool(home) and not home.get("stub"), home)
    check("[CP3-T15] the home entry still has name, scope, dir, reference and app",
          home.get("name") == "home" and home.get("scope") == "The home screen" and isinstance(home.get("dir"), str)
          and isinstance(home.get("reference"), dict) and isinstance(home.get("app"), dict), home)


def t16_scaffolded_spec_captures():
    print("CP3-T16: a scaffolded spec goes through capture and the stubs arrive marked in the record")
    check("[CP3-T16] setup: node is on PATH", have_node())
    d, sp = project_a(label="scaf")
    sc = scaffold(d)
    check("[CP3-T16] the scaffold run itself exits 0", sc.returncode == 0, (sc.returncode, out_of(sc)[-300:]))
    p, m = run_capture(sp)
    check("[CP3-T16] capture exits 0 and writes a manifest", p.returncode == 0 and isinstance(m, dict),
          (p.returncode, p.stderr[-300:]))
    states = {s.get("name"): s for s in (m or {}).get("states", [])}
    want = ["home", "cart", "checkout"] + ["stub-" + i for i in STUB_IDS]
    check("[CP3-T16] the manifest lists all original states plus the four stubs",
          sorted(states) == sorted(want), sorted(states))
    for cid in STUB_IDS:
        e = states.get("stub-" + cid, {})
        check(f"[CP3-T16] the stub-{cid} entry carries stub true and its case id",
              e.get("stub") is True and e.get("cases") == [cid], e)
    check("[CP3-T16] the manifest's spec_sha256 equals the sha256 of the scaffolded file",
          (m or {}).get("spec_sha256") == hashlib.sha256(sp.read_bytes()).hexdigest(),
          ((m or {}).get("spec_sha256"), hashlib.sha256(sp.read_bytes()).hexdigest()))
    check("[CP3-T16] the original states carry no stub mark",
          all(not states.get(n, {"stub": True}).get("stub") for n in ("home", "cart", "checkout")))


def t17_readme():
    print("CP3-T17: the test file is listed in the README's 'Running the tests' list")
    readme = README.read_text().splitlines()
    start = next((i for i, ln in enumerate(readme) if ln.strip() == "### Running the tests"), None)
    check("[CP3-T17] setup: README has a 'Running the tests' section", start is not None)
    if start is not None:
        end = next((i for i in range(start + 1, len(readme)) if readme[i].startswith("## ") or readme[i].startswith("### ")),
                   len(readme))
        n = [ln.strip() for ln in readme[start:end]].count("python3 tests/test_ui_capture_stub.py")
        check("[CP3-T17] the block holds the line `python3 tests/test_ui_capture_stub.py` exactly once", n == 1, n)
    check("[CP3-T17] this file is tests/test_ui_capture_stub.py", THIS.name == "test_ui_capture_stub.py" and THIS.exists())


def t18_inventory():
    print("CP3-T18: the test file matches neither inventory grep, or the inventory names it")
    # The two inventory greps from test-inventory.md, built at run time so this file does not match them itself.
    module_load = "spec_from" + "_file_location"
    source_scan = "|".join(["ENGINE" + r"\.read_text", "as" + "t" + r"\.parse", "as" + "t" + r"\.walk", "get" + "source"])
    src = THIS.read_text()
    hits = [pat for pat in (module_load, source_scan) if re.search(pat, src)]
    inv = INVENTORY.read_text()
    check("[CP3-T18] either no inventory pattern matches this file, or test-inventory.md names it",
          hits == [] or THIS.name in inv, (hits, THIS.name in inv))
    check("[CP3-T18] the inventory rule holds both ways: named exactly when a pattern matches",
          (THIS.name in inv) == bool(hits), (THIS.name in inv, hits))
    g = subprocess.run(["grep", "-lE", module_load + "|" + source_scan, str(THIS)], capture_output=True, text=True,
                       timeout=30)
    check("[CP3-T18] the live grep over this file finds nothing", g.returncode == 1 and g.stdout == "", g.stdout)


TESTS = [
    ("CP3-T01", t01_one_stub_per_gap), ("CP3-T02", t02_stub_shape), ("CP3-T03", t03_stub_width),
    ("CP3-T04", t04_existing_untouched), ("CP3-T05", t05_gap_on_existing_state), ("CP3-T06", t06_idempotent),
    ("CP3-T07", t07_started_stub_kept), ("CP3-T08", t08_new_case_later), ("CP3-T09", t09_locked_refuses),
    ("CP3-T10", t10_locked_still_warns), ("CP3-T11", t11_lock_is_per_step), ("CP3-T12", t12_no_run_no_lock),
    ("CP3-T13", t13_check_only_never_writes), ("CP3-T14", t14_nothing_missing),
    ("CP3-T15", t15_capture_keeps_stub_mark), ("CP3-T16", t16_scaffolded_spec_captures),
    ("CP3-T17", t17_readme), ("CP3-T18", t18_inventory),
]


def tx1_state_read_exit_writes_nothing():
    print("CP3-X1: when the run state cannot be read, scaffold stops with the engine's message and writes nothing")
    d, sp = run_case({"CP1": True, "CP2": False})
    state_file = d / ".ratchet/runs/f/state.json"
    good = state_file.read_bytes()
    before = sp.read_bytes()
    try:
        state_file.write_bytes(b"{ this is not json")
        p = scaffold(d, spec_rel=RUN_SPEC_REL)
    finally:
        state_file.write_bytes(good)
    o = out_of(p)
    check("[CP3-X1] the spec file is byte-for-byte unchanged", sp.read_bytes() == before)
    check("[CP3-X1] the command exits non-zero", p.returncode != 0, (p.returncode, o[-300:]))
    check("[CP3-X1] the engine's own message about state.json is shown", "state.json" in o, o[-400:])
    check("[CP3-X1] no stub is added", stubs_of(load(sp)) == [], [s.get("name") for s in load(sp)["states"]])


def tx2_run_spec_without_checkpoint_refuses():
    print("CP3-X2: inside a run, a spec that names no step is refused and nothing is written")
    for label, mutate in (("no checkpoint field", lambda s: s.pop("checkpoint")),
                          ("empty checkpoint", lambda s: s.update(checkpoint=""))):
        d, sp = run_case({"CP1": False, "CP2": False})
        spec = load(sp)
        mutate(spec)
        sp.write_text(json.dumps(spec))
        before = sp.read_bytes()
        p = scaffold(d, spec_rel=RUN_SPEC_REL)
        o = out_of(p)
        check(f"[CP3-X2] ({label}) the spec file is byte-for-byte unchanged", sp.read_bytes() == before)
        check(f"[CP3-X2] ({label}) the command exits non-zero", p.returncode != 0, (p.returncode, o[-300:]))
        check(f"[CP3-X2] ({label}) the message says the spec must name its step",
              "must name its step" in o, o[-300:])
        check(f"[CP3-X2] ({label}) no traceback", no_traceback(p), o[-300:])
    d, sp = project_a()
    spec = load(sp)
    spec.pop("checkpoint")
    sp.write_text(json.dumps(spec))
    p = scaffold(d)
    check("[CP3-X2] outside a run a spec without a checkpoint still scaffolds",
          p.returncode == 0 and len(stubs_of(load(sp))) == 4, (p.returncode, out_of(p)[-300:]))


TESTS.extend([("CP3-X1", tx1_state_read_exit_writes_nothing), ("CP3-X2", tx2_run_spec_without_checkpoint_refuses)])


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
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(e).__name__}: {e}")
            print(f"  ✗ [{cid}] CRASHED: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
