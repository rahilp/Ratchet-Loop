#!/usr/bin/env python3
"""
CP4 tests: check the capture before reviewers run.   python3 tests/test_ui_review_precheck.py

`ui_review.py review` and `ui_review.py stage`, given the test plan with `--cases tests/CPn.json`, run the same check
`lint` runs, against the capture manifest's states, before any reviewer starts:
  * every browser case the capture cannot show (no state, no journey steps, wrong width) is warned about on stderr, as
    one `WARN <case>: ...` line with the same wording `lint` prints; the review goes on (warn only);
  * a manifest state marked `stub: true` (a scaffolded stub nobody filled in) makes `review` stop BEFORE any reviewer
    starts and before the manifest is touched: non-zero exit that is not 4 (the hand-off code), stderr naming the stub
    and the capture spec `states/CP1.json`, even with no outside reviewer available and with or without --cases;
  * `stage` still stages (it warns, it never refuses) and marks a stub state in INPUT.md as a stub with no evidence;
  * a capture with no gaps and no stubs, no --cases, a plan with no browser cases or an unreadable plan path review
    exactly as before.

Everything is driven through the public command line, with subprocess only, in a throwaway git project with no Ratchet
run (an ad-hoc comparison). Stub `claude` and `codex` programs sit first on a private PATH; each logs one record the
moment it starts, copies its INPUT.md, and copies what the process had already printed to stderr (stderr goes to a
FILE named by STUB_STDERR_FILE, so "before any reviewer starts" is read off that snapshot). No real reviewer runs.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
"status": "passed"|"failed", "message": "..."}]}. A case that raises is recorded as failed, never dropped.
"""
from __future__ import annotations

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
import trust_helpers as th                              # noqa: E402

UI_REVIEW = ROOT / "skills" / "ratchet-ui-compare" / "scripts" / "ui_review.py"
README = ROOT / "README.md"
INVENTORY = HERE / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS = Path(__file__).resolve()

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP4-[TX]\d+[a-z]?)\]")


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

# One tiny valid PNG (1x1), used for every screenshot.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000000"
                    "02000154a24f5d0000000049454e44ae426082")

STUB_SCRIPT = r"""#!/bin/sh
# Snapshot of what the review process had printed to stderr when this reviewer started (taken first).
snap=""
if [ -n "$STUB_STDERR_FILE" ] && [ -f "$STUB_STDERR_FILE" ]; then snap=$(cat "$STUB_STDERR_FILE"); fi
me=$(basename "$0")
args="$*"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output-last-message) out="$2"; shift 2;;
    *) shift;;
  esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
role=unknown
case "$input $args" in
  *"Do the visual review"*) role=visual;;
  *"Do the behavior review"*) role=behavior;;
esac
f="@LOGDIR@/start-$me-$role-$$"
printf '%s|%s|%s\n' "$me" "$role" "$args" > "$f.log"
printf '%s' "$snap" > "$f.snap"
if [ -f INPUT.md ]; then cp INPUT.md "$f.input.md"; fi
rev='{"reviewer":"x","verdict":"PASS","differences":[]}'
if [ "$me" = claude ]; then printf '{"structured_output": %s}\n' "$rev"
else
  if [ -n "$out" ]; then printf '%s' "$rev" > "$out"; else printf '%s\n' "$rev"; fi
fi
"""

DASH = chr(0x2014)   # the long dash INPUT.md's overall-scope line uses, built here so this file has none
T01_TITLE = "Cart total updates"
T02_TITLE = "Checkout works on a phone"
T03_TITLE = "Error banner shows"
T04_TITLE = "Menu opens"
T05_TITLE = "Total is summed"
PHONE = {"width": 390, "height": 844}
DESK = {"width": 1280, "height": 800}
STEP_A = {"click": "#open"}
STEP_B = {"press": "Escape"}


def plan_full():
    return {"checkpoint": "CP1", "cases": [
        {"id": "CP1-T01", "title": T01_TITLE, "layer": "browser"},
        {"id": "CP1-T02", "title": T02_TITLE, "layer": "browser", "viewport": dict(PHONE)},
        {"id": "CP1-T03", "title": T03_TITLE, "layer": "browser", "ui_state": "checkout-error"},
        {"id": "CP1-T04", "title": T04_TITLE, "layer": "browser"},
        {"id": "CP1-T05", "title": T05_TITLE, "layer": "code"},
    ]}


def plan_code_only():
    return {"checkpoint": "CP1", "cases": [
        {"id": "CP1-T05", "title": T05_TITLE, "layer": "code"},
        {"id": "CP1-T06", "title": "Rounding is exact", "layer": "code", "ui_state": "ghost"},
    ]}


def _side(shots: Path, target, journey_steps, with_shot=True):
    png = str(shots / "tiny.png")
    recs = [{"i": i + 1, "step": s, "ok": True, "screenshot": png, "url": f"http://{target}.invalid/"}
            for i, s in enumerate(journey_steps)]
    return {"target": target, "url": f"http://{target}.invalid/", "screenshot": png if with_shot else None, "aria": None,
            "steps": [], "journey": recs, "consoleErrors": []}


def _state(shots, name, scope, cases, steps, viewport=None, stub=None, with_shot=True):
    st = {"name": name, "scope": scope, "dir": str(shots / name),
          "reference": _side(shots, "reference", steps, with_shot), "app": _side(shots, "app", steps, with_shot)}
    if cases is not None:
        st["cases"] = cases
    # The state-level journey list AND the app.journey records, so either reading of a covered state passes.
    st["journey"] = [dict(s) for s in steps]
    if viewport is not None:
        st["viewport"] = dict(viewport)
    if stub is not None:
        st["stub"] = stub
    return st


def make_manifest(shots: Path, menu="ok", phone=PHONE, error_journey=True, stub=None):
    """The gap-free manifest, or a gapped/stubbed variant of it.
    menu: 'ok' | 'none' (no 'menu' state) | 'nojourney' (state kept, journey emptied everywhere).
    stub: None (no stub state), True / False (a 'stub-CP1-T04' state carrying that mark), 'absent' (no stub key at
    all, journey filled in). A stub state sits after 'cart'; with a stub the 'menu' state is dropped (the stub holds
    CP1-T04)."""
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "tiny.png").write_bytes(PNG)
    states = [_state(shots, "cart", "The cart screen", ["CP1-T01"], [STEP_A, STEP_B])]
    if stub is not None:
        if stub is True:
            states.append(_state(shots, "stub-CP1-T04", "STUB, UNREVIEWED: add the journey that demonstrates CP1-T04 "
                                 "(Menu opens), then remove stub", ["CP1-T04"], [], stub=True))
        else:
            filled = _state(shots, "stub-CP1-T04", "The menu screen", ["CP1-T04"], [STEP_A, STEP_B],
                            stub=(False if stub is False else None))
            states.append(filled)
    states.append(_state(shots, "checkout-phone", "The checkout screen on a phone", ["CP1-T02"], [STEP_A, STEP_B],
                         viewport=phone))
    states.append(_state(shots, "checkout-error", "The checkout error banner", None,
                         [STEP_A, STEP_B] if error_journey else []))
    if stub is None and menu != "none":
        states.append(_state(shots, "menu", "The menu", ["CP1-T04"], [STEP_A] if menu == "ok" else []))
    return {"checkpoint": "CP1", "spec_sha256": "0" * 64, "viewport": dict(DESK),
            "captured_at": "2026-01-02T03:04:05Z", "states": states}


def make_spec_from(manifest):
    """A states/CP1.json capture spec mirroring a manifest's states (name, cases, journey, own viewport)."""
    states = []
    for st in manifest["states"]:
        s = {"name": st["name"], "scope": st.get("scope", ""), "journey": [dict(x) for x in st.get("journey", [])]}
        if "cases" in st:
            s["cases"] = list(st["cases"])
        if "viewport" in st:
            s["viewport"] = dict(st["viewport"])
        states.append(s)
    return {"checkpoint": "CP1", "viewport": dict(manifest["viewport"]),
            "targets": {"reference": "http://reference.invalid/", "app": "http://app.invalid/"}, "states": states}


# ------------------------------------------------------------------------------------------------ the harness

class World:
    """A throwaway git project with no Ratchet run, a private env (host company google, so claude and codex are both
    outside reviewers), a private TMPDIR, and (optionally) stub claude and codex first on PATH."""

    def __init__(self, stubs=True):
        self.env = th.make_env("gemini")
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            self.env.pop(k, None)
        self.d = th.cp5y_repo()
        (self.d / ".ratchet").mkdir()
        (self.d / ".ratchet" / "config.json").write_text("{}")
        self.tmp = Path(_tmp.mkdtemp(prefix="ratchet-precheck-tmp-"))
        self.env["TMPDIR"] = str(self.tmp)
        self.env["PYTHONUNBUFFERED"] = "1"
        self.bin = Path(_tmp.mkdtemp(prefix="ratchet-precheck-bin-"))
        self.logs = self.bin / "logs"
        self.logs.mkdir()
        if stubs:
            for n in ("claude", "codex"):
                p = self.bin / n
                p.write_text(STUB_SCRIPT.replace("@LOGDIR@", str(self.logs)))
                p.chmod(0o755)
            self.env["PATH"] = f"{self.bin}:{self.env['PATH']}"
        self.shots = self.d / "shots"
        self.manifest_path = self.shots / "manifest.json"
        self.errfile = self.d / "stderr.txt"
        self.env["STUB_STDERR_FILE"] = str(self.errfile)

    def put_manifest(self, manifest):
        self.shots.mkdir(exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, indent=2))
        return self.manifest_path

    def put_plan(self, plan, rel="tests/CP1.json"):
        p = self.d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(plan))
        return rel

    def starts(self):
        """[(program, role, argv)] for every stub start, plus the snapshots and INPUT.md copies, by (program, role)."""
        recs = []
        for lf in sorted(self.logs.glob("start-*.log")):
            parts = lf.read_text().rstrip("\n").split("|", 2)
            if len(parts) == 3:
                base = lf.with_suffix("")
                snap = base.with_suffix(".snap")
                inp = base.with_suffix(".input.md")
                recs.append({"name": parts[0], "role": parts[1], "argv": parts[2],
                             "snap": snap.read_text() if snap.exists() else "",
                             "input": inp.read_text() if inp.exists() else None})
        return recs

    def leftovers(self):
        return sorted(p.name for p in self.tmp.iterdir()
                      if p.name.startswith(("ratchet-visual-", "ratchet-behavior-")))


class Res:
    pass


def run_ui(w, args, keep_err=True):
    """Run ui_review.py with stderr redirected to a FILE (so stubs can snapshot it) and stdout piped."""
    with open(w.errfile, "w+") as ef:
        p = subprocess.run([sys.executable, str(UI_REVIEW)] + list(args), cwd=str(w.d), env=w.env, text=True,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=ef, timeout=240)
    r = Res()
    r.rc, r.out = p.returncode, p.stdout
    r.err = w.errfile.read_text()
    r.all = r.out + "\n" + r.err
    return r


def review(manifest, cases="plan", plan=None, extra=(), stubs=True, keep=False, out="out"):
    """Run `review` in a fresh world. cases: 'plan' (the full plan, or `plan`) | None (no --cases) | a path string."""
    w = World(stubs=stubs)
    m = manifest(w.shots) if callable(manifest) else manifest
    mp = w.put_manifest(m)
    args = ["review", "--manifest", str(mp), "--out", str(w.d / out), "--scope", "all"]
    if cases == "plan":
        args += ["--cases", w.put_plan(plan if plan is not None else plan_full())]
    elif cases:
        args += ["--cases", cases]
    if keep:
        args.append("--keep")
    args += list(extra)
    r = run_ui(w, args)
    r.w, r.manifest_path, r.outdir = w, mp, w.d / out
    r.starts = w.starts()
    r.out_files = sorted(p.name for p in r.outdir.iterdir()) if r.outdir.is_dir() else []
    return r


def stage(manifest, cases="plan", plan=None):
    w = World(stubs=False)
    m = manifest(w.shots) if callable(manifest) else manifest
    mp = w.put_manifest(m)
    args = ["stage", "--manifest", str(mp), "--scope", "all"]
    if cases == "plan":
        args += ["--cases", w.put_plan(plan if plan is not None else plan_full())]
    r = run_ui(w, args)
    r.w = w
    return r


def gap_free(shots):
    return make_manifest(shots)


def warns(text):
    return [ln.strip() for ln in text.splitlines() if ln.lstrip().startswith("WARN")]


def roles_started(r):
    return sorted(s["role"] for s in r.starts)


def started_once_each(r):
    return roles_started(r) == ["behavior", "visual"]


def snap_warns(r):
    """The WARN lines each reviewer's stderr snapshot held when it started: a list of lists (one per start)."""
    return [warns(s["snap"]) for s in r.starts]


def norm_argv(argv):
    return re.sub(r"\S*ratchet-(?:visual|behavior)-[A-Za-z0-9_]+", "STAGED", argv)


def start_args(r):
    return sorted((s["name"], s["role"], norm_argv(s["argv"])) for s in r.starts)


def no_traceback(r):
    return "Traceback" not in r.all


def contains_ids(line, ids):
    return [i for i in ids if i in line]


ALL_IDS = ["CP1-T01", "CP1-T02", "CP1-T03", "CP1-T04", "CP1-T05"]


def combined(r):
    p = r.outdir / "combined.json"
    return json.loads(p.read_text()) if p.exists() else {}


def finished_fine(r):
    return r.rc == 0 and started_once_each(r) and "combined.json" in r.out_files


# ------------------------------------------------------------------------------------------------ the cases

def t01_no_state():
    print("CP4-T01: a browser case with no capture state is warned about before any reviewer starts")
    r = review(lambda s: make_manifest(s, menu="none"))
    check("[CP4-T01] the review ran to the end (exit 0, both reviewers once, combined.json)", finished_fine(r),
          (r.rc, roles_started(r), r.out_files, r.err[-300:]))
    sw = snap_warns(r)
    check("[CP4-T01] the stub's stderr snapshot exists for every started reviewer", len(sw) == 2, len(sw))
    for i, lines in enumerate(sw):
        check(f"[CP4-T01] before reviewer {i + 1} started, stderr held exactly one WARN line", len(lines) == 1, lines)
        line = lines[0] if lines else ""
        check(f"[CP4-T01] (start {i + 1}) the line names CP1-T04 and says 'no state'",
              "CP1-T04" in line and "no state" in line.lower(), line)
        check(f"[CP4-T01] (start {i + 1}) the line names no other case", contains_ids(line, ALL_IDS) == ["CP1-T04"], line)
    check("[CP4-T01] the whole run printed that one WARN line only once", len(warns(r.all)) == 1, warns(r.all))


def t02_no_journey():
    print("CP4-T02: a covered case whose state has no journey steps is warned about, naming the case and the state")
    r = review(lambda s: make_manifest(s, menu="nojourney"))
    check("[CP4-T02] the review ran to the end (exit 0)", finished_fine(r), (r.rc, roles_started(r), r.err[-300:]))
    sw = snap_warns(r)
    check("[CP4-T02] both reviewers' snapshots each held exactly one WARN line", len(sw) == 2 and all(len(x) == 1 for x in sw), sw)
    line = sw[0][0] if sw and sw[0] else ""
    check("[CP4-T02] the line names CP1-T04, the state 'menu' and the word 'journey'",
          "CP1-T04" in line and "menu" in line and "journey" in line.lower(), line)
    check("[CP4-T02] no other case is named", contains_ids(line, ALL_IDS) == ["CP1-T04"], line)


def t03_wrong_width():
    print("CP4-T03: a phone-width case whose state was captured at desktop width is warned about, naming both widths")
    r = review(lambda s: make_manifest(s, phone=DESK))
    check("[CP4-T03] the review ran to the end (exit 0)", finished_fine(r), (r.rc, roles_started(r), r.err[-300:]))
    sw = snap_warns(r)
    check("[CP4-T03] both snapshots each held exactly one WARN line", len(sw) == 2 and all(len(x) == 1 for x in sw), sw)
    line = sw[0][0] if sw and sw[0] else ""
    check("[CP4-T03] the line names CP1-T02, says width (or viewport) and holds both 390 and 1280",
          "CP1-T02" in line and ("width" in line.lower() or "viewport" in line.lower()) and "390" in line
          and "1280" in line, line)
    check("[CP4-T03] no other case is named", contains_ids(line, ALL_IDS) == ["CP1-T02"], line)
    ok = review(lambda s: make_manifest(s, phone=PHONE))
    check("[CP4-T03] the same manifest with the phone state at 390x844 gives no WARN line at all",
          finished_fine(ok) and warns(ok.all) == [], (ok.rc, warns(ok.all)))


def t04_three_gaps():
    print("CP4-T04: every gap gets its own named warning; covered cases and code cases are never named")
    r = review(lambda s: make_manifest(s, menu="none", phone=DESK, error_journey=False))
    check("[CP4-T04] the review ran to the end (exit 0)", finished_fine(r), (r.rc, roles_started(r), r.err[-300:]))
    sw = snap_warns(r)
    check("[CP4-T04] both snapshots exist", len(sw) == 2, len(sw))
    for i, lines in enumerate(sw):
        check(f"[CP4-T04] (start {i + 1}) exactly three WARN lines were already printed", len(lines) == 3, lines)
        named = sorted(sum((contains_ids(ln, ALL_IDS) for ln in lines), []))
        check(f"[CP4-T04] (start {i + 1}) one line each names CP1-T02, CP1-T03 and CP1-T04, and no line names two",
              named == ["CP1-T02", "CP1-T03", "CP1-T04"] and all(len(contains_ids(ln, ALL_IDS)) == 1 for ln in lines), lines)
        check(f"[CP4-T04] (start {i + 1}) no WARN line contains CP1-T01 or CP1-T05",
              not any("CP1-T01" in ln or "CP1-T05" in ln for ln in lines), lines)
    lines = sw[0] if sw else []
    by = {i: next((ln for ln in lines if i in ln), "") for i in ("CP1-T02", "CP1-T03", "CP1-T04")}
    check("[CP4-T04] each line says what is missing (width / journey / no state)",
          "width" in by["CP1-T02"].lower() and "journey" in by["CP1-T03"].lower()
          and "no state" in by["CP1-T04"].lower(), by)
    check("[CP4-T04] the whole run printed exactly those three WARN lines", len(warns(r.all)) == 3, warns(r.all))


def t05_inherit_and_ui_state():
    print("CP4-T05: a state inherits the capture's width and a case can name its state, without a false warning")
    ok = review(gap_free)
    check("[CP4-T05] the gap-free run finished (exit 0, both reviewers once)", finished_fine(ok),
          (ok.rc, roles_started(ok), ok.err[-300:]))
    check("[CP4-T05] the gap-free run prints no line containing WARN", "WARN" not in ok.all, warns(ok.all))
    cart = next((s for s in json.loads(ok.manifest_path.read_text())["states"] if s["name"] == "cart"), {})
    check("[CP4-T05] fixture check: 'cart' has no viewport of its own", "viewport" not in cart, cart.get("viewport"))
    twin = review(lambda s: make_manifest(s, menu="none"))
    tw = warns(twin.all)
    check("[CP4-T05] the twin without 'menu' prints exactly one WARN, for CP1-T04",
          len(tw) == 1 and "CP1-T04" in tw[0] and contains_ids(tw[0], ALL_IDS) == ["CP1-T04"], tw)


def t06_unchanged_when_no_gaps():
    print("CP4-T06: a capture with no gaps reviews exactly as before")
    base = review(gap_free, keep=True)
    nocases = review(gap_free, cases=None, keep=True)
    twin = review(lambda s: make_manifest(s, menu="none"), keep=True)
    check("[CP4-T06] gap-free run: exit 0, no WARN, no 'stub' or 'unreviewed' on stderr",
          base.rc == 0 and "WARN" not in base.all and "stub" not in base.err.lower()
          and "unreviewed" not in base.err.lower(), (base.rc, base.err[-300:]))
    check("[CP4-T06] exactly one visual and one behavior reviewer started", started_once_each(base), roles_started(base))
    check("[CP4-T06] the reviewers got the same arguments as in the no --cases run",
          bool(base.starts) and start_args(base) == start_args(nocases), (start_args(base), start_args(nocases)))
    check("[CP4-T06] out/ holds visual.json, behavior.json and combined.json and nothing else",
          base.out_files == ["behavior.json", "combined.json", "visual.json"], base.out_files)
    comb = combined(base)
    check("[CP4-T06] combined verdict PASS with 0 differences", comb.get("verdict") == "PASS"
          and comb.get("differences") == [], comb.get("verdict"))
    before = make_manifest(base.w.shots)
    after = json.loads(base.manifest_path.read_text())
    check("[CP4-T06] the manifest's states are deeply equal to what they were", after.get("states") == before["states"])
    check("[CP4-T06] the only key added to the manifest is review_scope",
          sorted(set(after) - set(before)) == ["review_scope"] and set(before) <= set(after), sorted(after))
    for s in base.starts:
        inp = s["input"] or ""
        role = s["role"]
        low = inp.lower()
        check(f"[CP4-T06] ({role}) INPUT.md has no STUB/unreviewed/no-evidence text",
              inp != "" and "stub" not in low and "unreviewed" not in low and "no evidence" not in low, inp[:200])
        lines = inp.splitlines()
        names = [re.match(r"## State `([^`]+)`", ln).group(1) for ln in lines if re.match(r"## State `", ln)]
        check(f"[CP4-T06] ({role}) INPUT.md keeps today's stable lines",
              f"# UI review input ({role})" in lines and "Checkpoint: CP1" in lines
              and f"Overall scope {DASH} judge ONLY this: all" in lines
              and names == ["cart", "checkout-phone", "checkout-error", "menu"], (names, lines[:6]))
    check("[CP4-T06] the gap-free run left the same files as the no --cases run (except the behavior case list)",
          nocases.out_files == base.out_files and nocases.rc == 0 and "WARN" not in nocases.all,
          (nocases.out_files, nocases.rc))
    tw = warns(twin.all)
    check("[CP4-T06] the twin (menu removed) warns once, for CP1-T04", len(tw) == 1 and "CP1-T04" in tw[0], tw)
    check("[CP4-T06] the twin starts the same reviewers with the same arguments and writes the same output files",
          twin.rc == 0 and start_args(twin) == start_args(base) and twin.out_files == base.out_files,
          (twin.rc, twin.out_files))


def t07_stub_stops_review():
    print("CP4-T07: an unfilled stub stops the review before any reviewer starts and says which stub to fill in")
    r = review(lambda s: make_manifest(s, stub=True))
    check("[CP4-T07] the exit code is neither 0 nor 4", r.rc not in (0, 4), r.rc)
    check("[CP4-T07] there is no traceback", no_traceback(r), r.err[-300:])
    check("[CP4-T07] neither claude nor codex was started", r.starts == [], r.starts)
    low = r.err.lower()
    check("[CP4-T07] stderr names 'stub-CP1-T04', the word 'stub' and the capture spec 'states/CP1.json'",
          "stub-CP1-T04" in r.err and "stub" in low and "states/CP1.json" in r.err, r.err[-400:])
    bad = [f for f in r.out_files if f in ("visual.json", "behavior.json", "combined.json")]
    check("[CP4-T07] out/ holds no visual.json, behavior.json or combined.json", bad == [], r.out_files)


def t08_refusal_independent():
    print("CP4-T08: the stub refusal does not depend on --cases and beats the hand-off to the host's own reviewers")
    a = review(lambda s: make_manifest(s, stub=True), cases=None)
    check("[CP4-T08] (a) no --cases: refused (exit not 0 and not 4), stub named, nothing started",
          a.rc not in (0, 4) and "stub-CP1-T04" in a.err and a.starts == [] and no_traceback(a), (a.rc, a.err[-300:]))
    b = review(lambda s: make_manifest(s, stub=True), stubs=False)
    check("[CP4-T08] (b) no outside reviewer: refused the same way (exit not 0 and not 4), stub named",
          b.rc not in (0, 4) and "stub-CP1-T04" in b.err and no_traceback(b), (b.rc, b.err[-300:]))
    check("[CP4-T08] (b) stdout holds no 'host_roles' hand-off and no staged-folder paths",
          "host_roles" not in b.out and "ratchet-visual-" not in b.all and "ratchet-behavior-" not in b.out,
          b.out[-300:])
    c = review(lambda s: make_manifest(s, stub=True), extra=["--visual", "claude", "--behavior", "codex"])
    check("[CP4-T08] (c) explicit --visual/--behavior: refused the same way, stub named, nothing started",
          c.rc not in (0, 4) and "stub-CP1-T04" in c.err and c.starts == [] and no_traceback(c), (c.rc, c.err[-300:]))


def t09_refusal_changes_nothing():
    print("CP4-T09: a refused review changes nothing: the capture manifest is left exactly as it was")
    w = World()
    mp = w.put_manifest(make_manifest(w.shots, stub=True))
    before = mp.read_bytes()
    out = w.d / "out"
    out.mkdir()
    plan = w.put_plan(plan_full())
    r = run_ui(w, ["review", "--manifest", str(mp), "--out", str(out), "--scope", "all", "--cases", plan])
    check("[CP4-T09] the review was refused (exit not 0 and not 4)", r.rc not in (0, 4), (r.rc, r.err[-300:]))
    check("[CP4-T09] the manifest file is byte-for-byte identical afterwards", mp.read_bytes() == before)
    check("[CP4-T09] no review_scope, stamp or fingerprint was written",
          not any(k in json.loads(mp.read_text()) for k in ("review_scope", "stamp", "fingerprint")))
    check("[CP4-T09] no staged ratchet-visual-* or ratchet-behavior-* folder was left in TMPDIR",
          w.leftovers() == [], w.leftovers())
    check("[CP4-T09] out/ holds no file", sorted(p.name for p in out.iterdir()) == [], sorted(p.name for p in out.iterdir()))
    check("[CP4-T09] no reviewer was started", w.starts() == [])


def t10_only_marked_stub_blocks():
    print("CP4-T10: only a state marked as a stub blocks; a filled-in state that kept stub false does not")
    t = review(lambda s: make_manifest(s, stub=True))
    check("[CP4-T10] stub true: refused, no reviewer started", t.rc not in (0, 4) and t.starts == [], (t.rc, t.starts))
    for label, mark in (("stub false", False), ("stub key absent", "absent")):
        r = review(lambda s, mark=mark: make_manifest(s, stub=mark))
        check(f"[CP4-T10] {label}: not refused (exit 0), both reviewers started once, combined verdict PASS",
              finished_fine(r) and combined(r).get("verdict") == "PASS", (r.rc, roles_started(r), r.err[-300:]))
        check(f"[CP4-T10] {label}: a filled-in state covers its case, so no WARN line", warns(r.all) == [], warns(r.all))


def t11_stage_marks_stub():
    print("CP4-T11: the reviewers' input marks a stub state plainly as a stub with no evidence")

    def stub_mid(shots):
        m = make_manifest(shots, stub=True)
        return m
    r = stage(stub_mid)
    check("[CP4-T11] stage exits 0 (it stages and warns; only review refuses)", r.rc == 0 and no_traceback(r), (r.rc, r.err[-300:]))
    try:
        dirs = json.loads(r.out)
    except ValueError:
        dirs = {}
    check("[CP4-T11] stage printed the two staged folders", set(dirs) == {"visual", "behavior"}, r.out[-200:])
    for role in ("visual", "behavior"):
        folder = Path(dirs.get(role, "/nonexistent"))
        f = folder / "INPUT.md"
        txt = f.read_text() if f.exists() else ""
        parts = re.split(r"(?m)^(?=## State )", txt)
        sec = {}
        for p in parts:
            m = re.match(r"## State `([^`]+)`", p)
            if m:
                sec[m.group(1)] = p
        stub = sec.get("stub-CP1-T04", "")
        check(f"[CP4-T11] ({role}) INPUT.md has a section for the stub state", stub != "", list(sec))
        check(f"[CP4-T11] ({role}) the stub section holds 'STUB, UNREVIEWED'", "STUB, UNREVIEWED" in stub, stub[:300])
        check(f"[CP4-T11] ({role}) the stub section says 'no evidence'", "no evidence" in stub.lower(), stub[:300])
        check(f"[CP4-T11] ({role}) the stub section says it must not be judged as passing",
              re.search(r"(do not|don't|must not|never)\b[^\n]{0,40}judg\w*[^\n]{0,40}pass", stub, re.I) is not None, stub[:400])
        check(f"[CP4-T11] ({role}) the stub section lists no screenshot, accessibility or journey file as evidence",
              stub != "" and not re.search(r"\.png|\.aria\.txt|journey step", stub), stub[:400])
        for name in ("cart", "checkout-phone"):
            other = sec.get(name, "")
            check(f"[CP4-T11] ({role}) the '{name}' section has none of those words",
                  other != "" and not any(k in other.lower() for k in ("stub", "unreviewed", "no evidence")), other[:200])


def t12_stage_warns_machine_readable():
    print("CP4-T12: staging for the host's own reviewers warns the same way and keeps its output machine-readable")
    g = stage(lambda s: make_manifest(s, menu="none"))
    w = warns(g.err)
    check("[CP4-T12] the gap run exits 0 and prints exactly one WARN line naming CP1-T04 on stderr",
          g.rc == 0 and len(w) == 1 and "CP1-T04" in w[0] and contains_ids(w[0], ALL_IDS) == ["CP1-T04"], (g.rc, w))
    try:
        obj = json.loads(g.out)
    except ValueError:
        obj = None
    check("[CP4-T12] stdout is exactly the JSON object {visual, behavior} with no WARN text",
          isinstance(obj, dict) and set(obj) == {"visual", "behavior"} and "WARN" not in g.out, g.out[-300:])
    check("[CP4-T12] both staged folders exist",
          isinstance(obj, dict) and all(Path(str(v)).is_dir() for v in obj.values()), obj)
    ok = stage(gap_free)
    check("[CP4-T12] the gap-free run exits 0 and prints no WARN", ok.rc == 0 and "WARN" not in ok.all, (ok.rc, warns(ok.all)))


def t13_warn_before_host_handoff():
    print("CP4-T13: the warning also comes before the hand-off to the host, without disturbing the hand-off")
    r = review(lambda s: make_manifest(s, menu="none"), stubs=False)
    check("[CP4-T13] it exits 4 as today", r.rc == 4, (r.rc, r.err[-300:]))
    try:
        hand = json.loads(r.out)
    except ValueError:
        hand = None
    roles = hand.get("host_roles") if isinstance(hand, dict) else None
    check("[CP4-T13] stdout still parses as the JSON hand-off with host_roles for visual and behavior",
          isinstance(roles, dict) and set(roles) == {"visual", "behavior"}, r.out[-300:])
    w = warns(r.err)
    check("[CP4-T13] stderr holds one WARN line naming CP1-T04", len(w) == 1 and "CP1-T04" in w[0], w)
    check("[CP4-T13] no WARN text is on stdout", "WARN" not in r.out, r.out[-200:])
    check("[CP4-T13] the staged folders named in the hand-off exist",
          isinstance(roles, dict) and bool(roles) and all(Path(str(v)).is_dir() for v in roles.values()), roles)


def t14_no_plan_no_warning():
    print("CP4-T14: no plan or a plan with no browser cases changes nothing; an explicitly passed unreadable plan stops the review")
    gapped = lambda s: make_manifest(s, menu="none")           # noqa: E731
    control = review(gapped)
    cw = warns(control.all)
    check("[CP4-T14] the control (gapped, full plan) prints one WARN naming CP1-T04",
          len(cw) == 1 and "CP1-T04" in cw[0], cw)
    runs = {"(a) a plan of only code cases": review(gapped, plan=plan_code_only()),
            "(b) no --cases": review(gapped, cases=None)}
    for label, r in runs.items():
        check(f"[CP4-T14] {label}: no WARN line, exit 0, both reviewers once, no traceback, same output files",
              warns(r.all) == [] and finished_fine(r) and no_traceback(r)
              and r.out_files == ["behavior.json", "combined.json", "visual.json"], (r.rc, warns(r.all), r.out_files, r.err[-200:]))

    def refusal_run(rel, plan_text):
        """Run `review` with --cases REL; PLAN_TEXT (or None for a missing file) is written there first."""
        w = World()
        mp = w.put_manifest(gapped(w.shots))
        before = mp.read_bytes()
        if plan_text is not None:
            (w.d / rel).parent.mkdir(parents=True, exist_ok=True)
            (w.d / rel).write_text(plan_text)
        r = run_ui(w, ["review", "--manifest", str(mp), "--out", str(w.d / "out"), "--scope", "all", "--cases", rel])
        r.w, r.manifest_path, r.outdir, r.rel, r.manifest_before = w, mp, w.d / "out", rel, before
        r.starts = w.starts()
        r.out_files = sorted(q.name for q in r.outdir.iterdir()) if r.outdir.is_dir() else []
        return r

    def fails_closed(r):
        """True only for a refusal: exit not 0 and not 4, no traceback, stderr names the plan path and says the capture
        check could not run, no reviewer started, nothing written to out/, manifest untouched."""
        before = getattr(r, "manifest_before", None)
        untouched = before is None or r.manifest_path.read_bytes() == before
        rel = getattr(r, "rel", "")
        return (r.rc not in (0, 4) and no_traceback(r) and bool(rel) and rel in r.err
                and "capture check could not run" in r.err.lower()
                and r.starts == [] and r.out_files == [] and untouched)

    normal = runs["(b) no --cases"]
    check("[CP4-T14] teeth: a silent-skip run (exit 0, reviewers started, like a normal review) is not counted as a refusal",
          fails_closed(normal) is False, (normal.rc, roles_started(normal)))
    refusals = {
        "(c) --cases at a file that does not exist": refusal_run("tests/does-not-exist.json", None),
        "(d) --cases at a file of malformed JSON": refusal_run("tests/bad-plan.json", "{not json"),
        "(e) --cases at a file that is a capture spec, not a test plan":
            refusal_run("tests/not-a-plan.json", json.dumps({"states": [{"name": "menu", "viewport": 1280}]})),
    }
    for label, r in refusals.items():
        check(f"[CP4-T14] {label}: refuses (exit not 0 or 4, no traceback, stderr names the path and says the capture check "
              "could not run), starts no reviewer, writes no file to out/, leaves the manifest byte-identical",
              fails_closed(r) is True, (r.rc, r.err[-300:], roles_started(r), r.out_files))


def t15_same_wording_as_lint():
    print("CP4-T15: starting a review prints the same warning wording as the standalone check")
    r = review(lambda s: make_manifest(s, menu="none", phone=DESK, error_journey=False))
    manifest = json.loads(r.manifest_path.read_text())
    w = r.w
    spec_path = w.d / "states" / "CP1.json"
    spec_path.parent.mkdir()
    spec_path.write_text(json.dumps(make_spec_from(make_manifest(w.d / "shots2", menu="none", phone=DESK, error_journey=False))))
    plan = w.put_plan(plan_full())
    lint = subprocess.run([sys.executable, str(UI_REVIEW), "lint", "--spec", "states/CP1.json", "--cases", plan],
                          cwd=str(w.d), env=w.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    lint_set = {ln.strip() for ln in warns(lint.stdout + "\n" + lint.stderr)}
    sw = snap_warns(r)
    review_set = {ln.strip() for ln in sw[0]} if sw else set()
    check("[CP4-T15] setup: lint printed three WARN lines naming CP1-T02, CP1-T03 and CP1-T04",
          lint.returncode == 0 and len(lint_set) == 3
          and sorted(sum((contains_ids(ln, ALL_IDS) for ln in lint_set), [])) == ["CP1-T02", "CP1-T03", "CP1-T04"],
          (lint.returncode, lint_set))
    check("[CP4-T15] review printed three WARN lines before its first reviewer", len(review_set) == 3 and len(sw[0]) == 3,
          sw)
    check("[CP4-T15] the set of WARN lines review printed equals lint's, line for line", review_set == lint_set,
          (sorted(review_set), sorted(lint_set)))
    check("[CP4-T15] the manifest in use really holds the same states", [s["name"] for s in manifest["states"]]
          == [s["name"] for s in json.loads(spec_path.read_text())["states"]])


def t16_readme():
    print("CP4-T16: the new test file is listed in the README's 'Running the tests' list")
    readme = README.read_text().splitlines()
    start = next((i for i, ln in enumerate(readme) if ln.strip() == "### Running the tests"), None)
    check("[CP4-T16] setup: README has a 'Running the tests' section", start is not None)
    if start is not None:
        end = next((i for i in range(start + 1, len(readme)) if readme[i].startswith("## ") or readme[i].startswith("### ")),
                   len(readme))
        n = [ln.strip() for ln in readme[start:end]].count("python3 tests/test_ui_review_precheck.py")
        check("[CP4-T16] the block holds the line `python3 tests/test_ui_review_precheck.py` exactly once", n == 1, n)
    check("[CP4-T16] this file is tests/test_ui_review_precheck.py",
          THIS.name == "test_ui_review_precheck.py" and THIS.exists())


def t17_inventory():
    print("CP4-T17: the test file matches neither inventory grep, or the inventory names it")
    # The two inventory greps from test-inventory.md, built at run time so this file does not match them itself.
    module_load = "spec_from" + "_file_location"
    source_scan = "|".join(["ENGINE" + r"\.read_text", "as" + "t" + r"\.parse", "as" + "t" + r"\.walk", "get" + "source"])
    src = THIS.read_text()
    hits = [pat for pat in (module_load, source_scan) if re.search(pat, src)]
    inv = INVENTORY.read_text()
    check("[CP4-T17] either no inventory pattern matches this file, or test-inventory.md names it",
          hits == [] or THIS.name in inv, (hits, THIS.name in inv))
    check("[CP4-T17] the inventory rule holds both ways: named exactly when a pattern matches",
          (THIS.name in inv) == bool(hits), (THIS.name in inv, hits))
    g = subprocess.run(["grep", "-lE", module_load + "|" + source_scan, str(THIS)], capture_output=True, text=True,
                       timeout=30)
    check("[CP4-T17] the live grep over this file finds nothing", g.returncode == 1 and g.stdout == "", g.stdout)


def x1_stub_refusal_still_warns_other_gaps():
    print("CP4-X1: a stub refusal still prints the WARN line for another gapped case")

    def stub_and_gap(shots):
        m = make_manifest(shots, stub=True)
        m["states"] = [st for st in m["states"] if st["name"] != "cart"]   # nothing shows CP1-T01 any more
        return m
    r = review(stub_and_gap)
    w = warns(r.err)
    check("[CP4-X1] refused: exit not 0 and not 4, the stub named, no traceback",
          r.rc not in (0, 4) and "stub-CP1-T04" in r.err and no_traceback(r), (r.rc, r.err[-300:]))
    check("[CP4-X1] stderr still carries a WARN line naming CP1-T01", any("CP1-T01" in ln for ln in w), w)
    check("[CP4-X1] no reviewer was started and out/ holds no file", r.starts == [] and r.out_files == [], (r.starts, r.out_files))


TESTS = [
    ("CP4-T01", t01_no_state), ("CP4-T02", t02_no_journey), ("CP4-T03", t03_wrong_width),
    ("CP4-T04", t04_three_gaps), ("CP4-T05", t05_inherit_and_ui_state), ("CP4-T06", t06_unchanged_when_no_gaps),
    ("CP4-T07", t07_stub_stops_review), ("CP4-T08", t08_refusal_independent),
    ("CP4-T09", t09_refusal_changes_nothing), ("CP4-T10", t10_only_marked_stub_blocks),
    ("CP4-T11", t11_stage_marks_stub), ("CP4-T12", t12_stage_warns_machine_readable),
    ("CP4-T13", t13_warn_before_host_handoff), ("CP4-T14", t14_no_plan_no_warning),
    ("CP4-T15", t15_same_wording_as_lint), ("CP4-T16", t16_readme), ("CP4-T17", t17_inventory),
]

TESTS.append(("CP4-X1", x1_stub_refusal_still_warns_other_gaps))


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
