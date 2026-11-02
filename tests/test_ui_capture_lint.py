#!/usr/bin/env python3
"""
CP2 tests: warn when a browser-only test case has nothing to be captured with.   python3 tests/test_ui_capture_lint.py

Before any reviewer runs, `ui_review.capture_gaps(test_plan, capture_spec)` lists every browser-layer case that the
capture spec cannot show: no state covers it ("no-state"), the covering state has no journey steps ("no-journey"),
or the covering state is captured at a different width than the case asks for ("viewport"). The `lint` subcommand
(`ui_review.py lint --spec states/CPn.json --cases tests/CPn.json`) prints one WARN line per gap and exits 0;
unreadable input exits non-zero with a one-line message naming the file.

Entry points used:
  * `ui_review.capture_gaps(...)`, imported in-process through sys.path (the way tests/test_gates.py imports it);
  * `python3 skills/ratchet-ui-compare/scripts/ui_review.py lint ...`, run by subprocess in a temp folder that has no
    .ratchet folder.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}. A case that raises (for example because capture_gaps does not exist
yet) is recorded as failed, never dropped.
"""
from __future__ import annotations
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text

import copy
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
README = README_DOCS
INVENTORY = HERE / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS = Path(__file__).resolve()

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP2-[TX]\d+[a-z]?)\]")


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

_STATE = {"n": 0}


def workdir():
    if "wd" not in _STATE:
        _STATE["wd"] = _tmp.mkdtemp(prefix="cp2-lint-")
    return Path(_STATE["wd"])


def fresh_dir(label="case"):
    _STATE["n"] += 1
    d = workdir() / f"{label}-{_STATE['n']}"
    d.mkdir()
    return d


def capture_gaps_fn():
    """Imports ui_review in-process (inside the test body, so a missing function fails one case, not the file)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import ui_review
    return ui_review.capture_gaps


def gaps(plan, spec):
    return capture_gaps_fn()(plan, spec)


def env():
    e = {k: v for k, v in os.environ.items() if k not in ("RATCHET_ROLE", "RATCHET_ROOT", "RATCHET_HUMAN_OVERRIDE")}
    e["RATCHET_HOME"] = str(workdir() / "home")
    return e


def run_ui(args, cwd):
    return subprocess.run([sys.executable, str(UI_REVIEW)] + list(args), capture_output=True, text=True, cwd=str(cwd),
                          env=env(), timeout=120)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj) if not isinstance(obj, str) else obj)


def lint(spec, plan, label="lint"):
    """Writes states/CP2.json and tests/CP2.json in a fresh folder with no .ratchet and runs the lint command.
    Returns (proc, folder, warn_lines, output)."""
    d = fresh_dir(label)
    (d / "states").mkdir()
    (d / "tests").mkdir()
    write_json(d / "states" / "CP2.json", spec)
    write_json(d / "tests" / "CP2.json", plan)
    p = run_ui(["lint", "--spec", "states/CP2.json", "--cases", "tests/CP2.json"], d)
    out = p.stdout + "\n" + p.stderr
    warns = [ln for ln in out.splitlines() if ln.lstrip().startswith("WARN")]
    return p, d, warns, out


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


def state(name, journey=JOURNEY, cases=None, viewport=None):
    s = {"name": name}
    if journey is not None:
        s["journey"] = journey
    if cases is not None:
        s["cases"] = cases
    if viewport is not None:
        s["viewport"] = viewport
    return s


def spec_of(states, viewport=None):
    s = {"checkpoint": "CP2", "targets": {"reference": "http://reference.invalid/", "app": "http://app.invalid/"},
         "states": states}
    if viewport is not None:
        s["viewport"] = viewport
    return s


APPROVE = "Approve button shows a spinner while saving"


def case_gaps(gs, cid):
    return [g for g in gs if isinstance(g, dict) and g.get("case") == cid]


def no_traceback(proc):
    return "Traceback" not in proc.stdout and "Traceback" not in proc.stderr


# ------------------------------------------------------------------------------------------------ cases

def t01_no_state():
    print("CP2-T01: a browser case with no capture state is named in a warning")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    spec = spec_of([state("empty", cases=["CP2-T09"])], vp(1280, 800))
    gs = gaps(plan, spec)
    check("[CP2-T01] capture_gaps returns exactly one gap", isinstance(gs, list) and len(gs) == 1, gs)
    g = gs[0] if gs else {}
    check("[CP2-T01] the gap is for CP2-T07 with the plan's title and missing 'no-state'",
          g.get("case") == "CP2-T07" and g.get("title") == APPROVE and g.get("missing") == "no-state", g)
    check("[CP2-T01] the gap carries a non-empty detail string", isinstance(g.get("detail"), str) and g["detail"].strip(), g)
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T01] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T01] lint prints exactly one WARN line", len(warns) == 1, warns)
    check("[CP2-T01] the WARN line names CP2-T07 and says 'no state'",
          len(warns) == 1 and "CP2-T07" in warns[0] and "no state" in warns[0].lower(), warns)


def t02_no_journey():
    print("CP2-T02: a covered case whose state has no journey steps is warned about")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    spec = spec_of([state("saving", journey=[], cases=["CP2-T07"]), state("other", cases=[])], vp(1280, 800))
    gs = gaps(plan, spec)
    check("[CP2-T02] exactly one gap, 'no-journey' for CP2-T07",
          len(gs) == 1 and gs[0].get("case") == "CP2-T07" and gs[0].get("missing") == "no-journey", gs)
    check("[CP2-T02] the gap detail names the state 'saving'", gs and "saving" in str(gs[0].get("detail")), gs)
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T02] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T02] one WARN line naming CP2-T07, 'saving' and 'journey'",
          len(warns) == 1 and all(w in warns[0] for w in ("CP2-T07", "saving", "journey")), warns)


def t03_journey_key_missing():
    print("CP2-T03: a state with no journey key counts as no journey steps")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    spec = spec_of([state("saving", journey=None, cases=["CP2-T07"])], vp(1280, 800))
    check("[CP2-T03] the fixture state really has no journey key", "journey" not in spec["states"][0])
    gs = gaps(plan, spec)
    check("[CP2-T03] one 'no-journey' gap for CP2-T07 and no KeyError",
          len(gs) == 1 and gs[0].get("case") == "CP2-T07" and gs[0].get("missing") == "no-journey", gs)


def t04_phone_case_desktop_state():
    print("CP2-T04: a phone-width case whose state is captured at desktop width is warned about")
    plan = {"cases": [browser("CP2-T07", APPROVE, viewport=vp(390, 844))]}
    spec = spec_of([state("saving", cases=["CP2-T07"])], vp(1280, 800))
    gs = gaps(plan, spec)
    check("[CP2-T04] exactly one 'viewport' gap for CP2-T07",
          len(gs) == 1 and gs[0].get("case") == "CP2-T07" and gs[0].get("missing") == "viewport", gs)
    det = str(gs[0].get("detail")) if gs else ""
    check("[CP2-T04] the detail names both widths and says 'width'",
          "390x844" in det and "1280x800" in det and "width" in det.lower(), det)
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T04] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T04] the WARN line names CP2-T07, 390x844, 1280x800 and 'width'",
          len(warns) == 1 and all(w in warns[0] for w in ("CP2-T07", "390x844", "1280x800"))
          and "width" in warns[0].lower(), warns)


def t05_phone_state_beside_desktop():
    print("CP2-T05: a phone-width state beside desktop states covers a phone-width case")
    plan = {"cases": [browser("CP2-T07", APPROVE, viewport=vp(390, 844))]}
    spec = spec_of([state("list", cases=["CP2-T03"]),
                    state("saving-phone", cases=["CP2-T07"], viewport=vp(390, 844))], vp(1280, 800))
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T05] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T05] no WARN line", "WARN" not in out and warns == [], out)
    check("[CP2-T05] capture_gaps returns an empty list", gaps(plan, spec) == [])


def t06_width_fallbacks():
    print("CP2-T06: state width falls back to the spec width, then to 1280x800")
    a = gaps({"cases": [browser("CP2-T07", APPROVE, viewport=vp(1440, 900))]},
             spec_of([state("saving", cases=["CP2-T07"])], vp(1440, 900)))
    check("[CP2-T06] (a) case 1440x900, spec 1440x900, state none: no gap", a == [], a)
    b = gaps({"cases": [browser("CP2-T07", APPROVE, viewport=vp(1280, 800))]},
             spec_of([state("saving", cases=["CP2-T07"])]))
    check("[CP2-T06] (b) case 1280x800, no width anywhere: no gap (default 1280x800)", b == [], b)
    c = gaps({"cases": [browser("CP2-T07", APPROVE, viewport=vp(1280, 800))]},
             spec_of([state("saving", cases=["CP2-T07"])], vp(390, 844)))
    check("[CP2-T06] (c) case 1280x800, spec 390x844, state none: one 'viewport' gap",
          len(c) == 1 and c[0].get("missing") == "viewport", c)


def t07_all_covered():
    print("CP2-T07: a plan whose browser cases are all covered prints no warning")
    plan = {"cases": [browser("CP2-T07", APPROVE, viewport=vp(1280, 800)),
                      browser("CP2-T08", "Checkout shows the error", ui_state="checkout-error")]}
    spec = spec_of([state("saving", cases=["CP2-T07"]), state("checkout-error")], vp(1280, 800))
    check("[CP2-T07] capture_gaps returns an empty list", gaps(plan, spec) == [])
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T07] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T07] no line contains WARN", "WARN" not in out, out)


def t08_code_only():
    print("CP2-T08: a plan with only code cases prints no warning, even with an empty spec")
    plan = {"cases": [code("CP2-T01", "A code case"), code("CP2-T02", "Another", ui_state="ghost")]}
    check("[CP2-T08] capture_gaps returns an empty list", gaps(plan, {}) == [])
    p, d, warns, out = lint({}, plan)
    check("[CP2-T08] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T08] no WARN line", "WARN" not in out, out)


def t09_code_cases_not_named():
    print("CP2-T09: code cases are not warned about when a browser case in the same plan is")
    plan = {"cases": [code("CP2-T01", "Code one"), code("CP2-T02", "Code two", ui_state="ghost"),
                      browser("CP2-T07", APPROVE)]}
    gs = gaps(plan, spec_of([]))
    check("[CP2-T09] exactly one gap, for CP2-T07",
          isinstance(gs, list) and len(gs) == 1 and gs[0].get("case") == "CP2-T07", gs)


def t10_empty_shapes():
    print("CP2-T10: a plan with no cases, a plan {} and a spec with no states key are handled")
    st = spec_of([state("saving", cases=["CP2-T07"])])
    check("[CP2-T10] (a) {'cases': []} gives []", gaps({"cases": []}, st) == [])
    check("[CP2-T10] (b) {} gives []", gaps({}, st) == [])
    c = gaps({"cases": [browser("CP2-T07", APPROVE)]}, {})
    check("[CP2-T10] (c) a browser case with a spec {} gives a 'no-state' gap",
          len(c) == 1 and c[0].get("case") == "CP2-T07" and c[0].get("missing") == "no-state", c)


def t11_id_spellings():
    print("CP2-T11: a state covers a case when its cases list holds the id in any usual spelling")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    for spelling in ("cp2_t07", "CP2_T07", "CP2-T07"):
        gs = gaps(plan, spec_of([state("saving", cases=[spelling])]))
        check(f"[CP2-T11] spelling {spelling!r} covers CP2-T07", gs == [], gs)
    other = gaps(plan, spec_of([state("saving", cases=["CP2-T70"])]))
    check("[CP2-T11] a state listing CP2-T70 instead yields a 'no-state' gap",
          len(other) == 1 and other[0].get("missing") == "no-state", other)


def t12_ui_state_names_state():
    print("CP2-T12: a state covers a case when the case's ui_state names that state")
    plan = {"cases": [browser("CP2-T08", "Checkout shows the error", ui_state="checkout-error")]}
    gs = gaps(plan, spec_of([state("checkout-error")]))
    check("[CP2-T12] no gap", gs == [], gs)


def t13_ui_state_missing_state():
    print("CP2-T13: a ui_state naming a state that does not exist is warned about, naming it")
    plan = {"cases": [browser("CP2-T08", "Checkout shows the error", ui_state="checkout-error")]}
    spec = spec_of([state("checkout-ok", cases=["CP2-T01"])])
    gs = gaps(plan, spec)
    check("[CP2-T13] one 'no-state' gap for CP2-T08",
          len(gs) == 1 and gs[0].get("case") == "CP2-T08" and gs[0].get("missing") == "no-state", gs)
    check("[CP2-T13] the detail names 'checkout-error'", gs and "checkout-error" in str(gs[0].get("detail")), gs)
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T13] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T13] one WARN line naming CP2-T08 and 'checkout-error'",
          len(warns) == 1 and "CP2-T08" in warns[0] and "checkout-error" in warns[0], warns)


def t14_case_without_width():
    print("CP2-T14: a case with no width of its own does not care about the state's width")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    gs = gaps(plan, spec_of([state("saving-phone", cases=["CP2-T07"], viewport=vp(390, 844))], vp(1280, 800)))
    check("[CP2-T14] no gap", gs == [], gs)


def t15_one_good_state_enough():
    print("CP2-T15: with several covering states one good one is enough; if all are bad the case is warned")
    plan = {"cases": [browser("CP2-T07", APPROVE, ui_state="state-b", viewport=vp(390, 844))]}
    a = state("state-a", journey=[], cases=["CP2-T07"], viewport=vp(390, 844))
    good = gaps(plan, spec_of([a, state("state-b", viewport=vp(390, 844))], vp(1280, 800)))
    check("[CP2-T15] first variant (B journey present, phone width): no gap", good == [], good)
    bad = gaps(plan, spec_of([a, state("state-b")], vp(1280, 800)))
    check("[CP2-T15] second variant (B at 1280x800): a gap for CP2-T07, not silent",
          len(bad) >= 1 and all(g.get("case") == "CP2-T07" for g in bad), bad)


def t16_both_problems():
    print("CP2-T16: a case that is both journey-less and the wrong width reports both problems")
    plan = {"cases": [browser("CP2-T07", APPROVE, viewport=vp(390, 844))]}
    spec = spec_of([state("saving", journey=[], cases=["CP2-T07"])], vp(1280, 800))
    gs = gaps(plan, spec)
    check("[CP2-T16] two gaps for CP2-T07, 'no-journey' and 'viewport'",
          len(gs) == 2 and all(g.get("case") == "CP2-T07" for g in gs)
          and sorted(g.get("missing") for g in gs) == ["no-journey", "viewport"], gs)
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T16] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T16] two WARN lines, one saying 'journey' and one saying 'width'",
          len(warns) == 2 and sum("journey" in w.lower() for w in warns) >= 1
          and sum("width" in w.lower() for w in warns) >= 1
          and any("journey" in w.lower() for w in warns) and any("width" in w.lower() for w in warns), warns)
    check("[CP2-T16] each WARN line names the case", all("CP2-T07" in w for w in warns), warns)


def t17_order_and_names():
    print("CP2-T17: every gap warned by name, one line each, in plan order; covered cases not named")
    plan = {"cases": [browser("CP2-T05", "First thing visible", viewport=vp(1280, 800)),
                      browser("CP2-T06", "Second thing visible"),
                      browser("CP2-T07", "Third thing visible"),
                      browser("CP2-T08", "Fourth thing visible", viewport=vp(390, 844)),
                      code("CP2-T01", "A code case")]}
    spec = spec_of([state("alpha", cases=["CP2-T05"]),
                    state("beta", journey=[], cases=["CP2-T07"]),
                    state("gamma", cases=["CP2-T08"])], vp(1280, 800))
    p, d, warns, out = lint(spec, plan)
    check("[CP2-T17] lint exits 0", p.returncode == 0, (p.returncode, out))
    check("[CP2-T17] exactly three WARN lines", len(warns) == 3, warns)
    if len(warns) == 3:
        check("[CP2-T17] in plan order CP2-T06, CP2-T07, CP2-T08",
              "CP2-T06" in warns[0] and "CP2-T07" in warns[1] and "CP2-T08" in warns[2], warns)
        check("[CP2-T17] each line says what is missing (no state / journey / width)",
              "no state" in warns[0].lower() and "journey" in warns[1].lower() and "width" in warns[2].lower(), warns)
    check("[CP2-T17] CP2-T05 and CP2-T01 appear in no WARN line",
          not any("CP2-T05" in w or "CP2-T01" in w for w in warns), warns)


def tree(d):
    return sorted(str(p.relative_to(d)) for p in Path(d).rglob("*"))


def t18_read_only():
    print("CP2-T18: the check only reads: it writes no file and changes neither input")
    plan = {"cases": [browser("CP2-T07", APPROVE, viewport=vp(390, 844)), browser("CP2-T09", "No state")]}
    spec = spec_of([state("saving", cases=["CP2-T07"])], vp(1280, 800))
    d = fresh_dir("ro")
    (d / "states").mkdir()
    (d / "tests").mkdir()
    sp, cp = d / "states" / "CP2.json", d / "tests" / "CP2.json"
    write_json(sp, spec)
    write_json(cp, plan)
    before = (sp.read_bytes(), cp.read_bytes(), tree(d))
    p = run_ui(["lint", "--spec", "states/CP2.json", "--cases", "tests/CP2.json"], d)
    check("[CP2-T18] lint ran and exits 0", p.returncode == 0, (p.returncode, p.stdout, p.stderr))
    check("[CP2-T18] both files are byte-identical afterwards", (sp.read_bytes(), cp.read_bytes()) == before[:2])
    check("[CP2-T18] no file or folder was created", tree(d) == before[2], tree(d))
    plan2, spec2 = copy.deepcopy(plan), copy.deepcopy(spec)
    gs = gaps(plan2, spec2)
    check("[CP2-T18] the fixture has gaps (so the check really ran)", isinstance(gs, list) and len(gs) == 2, gs)
    check("[CP2-T18] the dicts equal their copies afterwards", plan2 == plan and spec2 == spec)


def t19_unreadable_input():
    print("CP2-T19: missing or unreadable input files fail cleanly, without a traceback")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    spec = spec_of([state("saving", cases=["CP2-T07"])])
    variants = [
        ("a", "nope-spec.json", None, "spec file does not exist"),
        ("b", None, "nope-cases.json", "cases file does not exist"),
        ("c", "bad-spec.json", None, "spec is invalid JSON"),
        ("d", None, "bad-cases.json", "cases file is invalid JSON"),
        ("e", "list-spec.json", None, "spec is a JSON list"),
    ]
    for tag, bad_spec, bad_cases, what in variants:
        d = fresh_dir(f"bad-{tag}")
        write_json(d / "good-spec.json", spec)
        write_json(d / "good-cases.json", plan)
        if bad_spec == "bad-spec.json":
            (d / bad_spec).write_text("{not json")
        if bad_spec == "list-spec.json":
            (d / bad_spec).write_text("[1, 2]")
        if bad_cases == "bad-cases.json":
            (d / bad_cases).write_text("{not json")
        sfile = bad_spec or "good-spec.json"
        cfile = bad_cases or "good-cases.json"
        p = run_ui(["lint", "--spec", sfile, "--cases", cfile], d)
        out = (p.stdout + p.stderr).strip()
        offender = bad_spec or bad_cases
        check(f"[CP2-T19] ({tag}) {what}: exits non-zero", p.returncode != 0, p.returncode)
        check(f"[CP2-T19] ({tag}) prints no traceback", no_traceback(p), out[-300:])
        check(f"[CP2-T19] ({tag}) prints a one-line message", len([ln for ln in out.splitlines() if ln.strip()]) == 1, out)
        check(f"[CP2-T19] ({tag}) the message names {offender}", offender in out, out)
        check(f"[CP2-T19] ({tag}) it is not a gap warning", "WARN" not in out, out)


def t20_odd_shapes():
    print("CP2-T20: oddly shaped but valid JSON does not crash the check")
    plan = {"cases": [browser("CP2-T07", APPROVE, viewport=vp(390, 844))]}
    plain = {"cases": [browser("CP2-T07", APPROVE)]}

    def run(label, p, s, must_report=False):
        try:
            gs = gaps(p, s)
        except Exception as e:  # noqa: BLE001
            check(f"[CP2-T20] {label}: no exception", False, f"{type(e).__name__}: {e}")
            return None
        check(f"[CP2-T20] {label}: a list of gaps comes back", isinstance(gs, list), gs)
        if must_report:
            check(f"[CP2-T20] {label}: the case is still reported, never silently covered",
                  isinstance(gs, list) and any(g.get("case") == "CP2-T07" for g in gs), gs)
        return gs

    run("states is a string", plain, {"states": "oops"}, True)
    run("states is a dict", plain, {"states": {"saving": {}}}, True)
    run("state entries are a string, null and a number", plain, {"states": ["saving", None, 5]}, True)
    run("journey is a string", plain, {"states": [{"name": "saving", "cases": ["CP2-T07"], "journey": "click it"}]})
    run("viewport is a string on the spec", plan,
        {"viewport": "wide", "states": [state("saving", cases=["CP2-T07"])]})
    run("viewport is a string on the state", plan,
        {"states": [{"name": "saving", "cases": ["CP2-T07"], "journey": JOURNEY, "viewport": "phone"}]})
    run("viewport with no height", plan,
        {"states": [{"name": "saving", "cases": ["CP2-T07"], "journey": JOURNEY, "viewport": {"width": 390}}]})
    run("the case's own viewport has no height", {"cases": [browser("CP2-T07", APPROVE, viewport={"width": 390})]},
        spec_of([state("saving", cases=["CP2-T07"])]))
    run("a state's cases is the string 'CP2-T07'", plain,
        {"states": [{"name": "saving", "cases": "CP2-T07", "journey": JOURNEY}]})
    run("a state's cases holds junk", plain,
        {"states": [{"name": "saving", "cases": [None, 7, {"x": 1}], "journey": JOURNEY}]}, True)
    run("a cases entry is not an object", {"cases": ["CP2-T07", None, 3, browser("CP2-T07", APPROVE)]},
        spec_of([]), True)
    run("plan cases is not a list", {"cases": "CP2-T07"}, spec_of([]))
    run("spec is a list", plain, [])
    gs = run("a browser case with no id", {"cases": [{"layer": "browser", "title": "Nameless case"}]}, spec_of([]))
    check("[CP2-T20] a browser case with no id is reported under a placeholder name, not dropped",
          isinstance(gs, list) and len(gs) == 1 and isinstance(gs[0].get("case"), str) and gs[0]["case"].strip() != "",
          gs)
    p, d, warns, out = lint({"states": "oops"}, plain, "odd")
    check("[CP2-T20] lint on a states-is-a-string spec: exits 0 with no traceback",
          p.returncode == 0 and no_traceback(p), (p.returncode, out[-300:]))
    check("[CP2-T20] lint still warns about CP2-T07 there", len(warns) == 1 and "CP2-T07" in warns[0], warns)


def t21_command_line():
    print("CP2-T21: the command line lists lint next to review, stage and combine")
    d = fresh_dir("help")
    h = run_ui(["--help"], d)
    out = h.stdout + h.stderr
    check("[CP2-T21] --help exits 0 and lists lint, review, stage and combine",
          h.returncode == 0 and all(re.search(r"\b" + w + r"\b", out) for w in ("lint", "review", "stage", "combine")),
          (h.returncode, out[:300]))
    lh = run_ui(["lint", "--help"], d)
    lout = lh.stdout + lh.stderr
    check("[CP2-T21] lint --help exits 0 and shows --spec and --cases",
          lh.returncode == 0 and "--spec" in lout and "--cases" in lout, (lh.returncode, lout[:300]))
    bare = run_ui([], d)
    check("[CP2-T21] no subcommand still exits non-zero", bare.returncode != 0, bare.returncode)
    check("[CP2-T21] no subcommand prints no traceback", no_traceback(bare), bare.stderr[-300:])


def t22_readme():
    print("CP2-T22: the test file is listed once in the README's 'Running the tests' block")
    readme = README.read_text().splitlines()
    start = next((i for i, ln in enumerate(readme) if ln.strip() == "### Running the tests"), None)
    check("[CP2-T22] setup: README has a 'Running the tests' section", start is not None)
    if start is not None:
        end = next((i for i in range(start + 1, len(readme)) if readme[i].startswith("## ") or readme[i].startswith("### ")),
                   len(readme))
        lines = [ln.strip() for ln in readme[start:end]]
        n = lines.count("python3 tests/test_ui_capture_lint.py")
        check("[CP2-T22] the block holds the line `python3 tests/test_ui_capture_lint.py` exactly once", n == 1, n)
    check("[CP2-T22] tests/test_ui_capture_lint.py exists", THIS.name == "test_ui_capture_lint.py" and THIS.exists())


def t23_inventory():
    print("CP2-T23: the test file matches neither inventory grep, or the inventory names it")
    # The two inventory greps from test-inventory.md, built at run time so this file does not match them itself.
    module_load = "spec_from" + "_file_location"
    source_scan = "|".join(["ENGINE" + r"\.read_text", "as" + "t" + r"\.parse", "as" + "t" + r"\.walk", "get" + "source"])
    src = THIS.read_text()
    hits = [p for p in (module_load, source_scan) if re.search(p, src)]
    inv = INVENTORY.read_text()
    check("[CP2-T23] either no inventory pattern matches this file, or test-inventory.md names it",
          hits == [] or THIS.name in inv, (hits, THIS.name in inv))
    check("[CP2-T23] the inventory rule holds both ways: named exactly when a pattern matches",
          (THIS.name in inv) == bool(hits), (THIS.name in inv, hits))
    g = subprocess.run(["grep", "-lE", module_load + "|" + source_scan, str(THIS)], capture_output=True, text=True,
                       timeout=30)
    check("[CP2-T23] the live grep over this file finds nothing", g.returncode == 1 and g.stdout == "", g.stdout)
    check("[CP2-T23] this file imports ui_review through sys.path", "sys.path.insert" in src and "import ui_review" in src)


TESTS = [
    ("CP2-T01", t01_no_state), ("CP2-T02", t02_no_journey), ("CP2-T03", t03_journey_key_missing),
    ("CP2-T04", t04_phone_case_desktop_state), ("CP2-T05", t05_phone_state_beside_desktop),
    ("CP2-T06", t06_width_fallbacks), ("CP2-T07", t07_all_covered), ("CP2-T08", t08_code_only),
    ("CP2-T09", t09_code_cases_not_named), ("CP2-T10", t10_empty_shapes), ("CP2-T11", t11_id_spellings),
    ("CP2-T12", t12_ui_state_names_state), ("CP2-T13", t13_ui_state_missing_state),
    ("CP2-T14", t14_case_without_width), ("CP2-T15", t15_one_good_state_enough), ("CP2-T16", t16_both_problems),
    ("CP2-T17", t17_order_and_names), ("CP2-T18", t18_read_only), ("CP2-T19", t19_unreadable_input),
    ("CP2-T20", t20_odd_shapes), ("CP2-T21", t21_command_line), ("CP2-T22", t22_readme),
    ("CP2-T23", t23_inventory),
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
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(e).__name__}: {e}")
            print(f"  ✗ [{cid}] CRASHED: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


# ------------------------------------------------------------------------------------------------ adversarial round 1
# Note: keep this file clear of the test-inventory grep patterns (CP2-T23 checks it); do not add code that reads
# engine source or loads a module by spec.

def tx1_swapped_paths():
    print("CP2-X1: swapped --spec and --cases fail with one line, no WARN lines, no traceback")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    spec = spec_of([state("saving", cases=["CP2-T07"])], vp(1280, 800))
    d = fresh_dir("swap")
    write_json(d / "spec.json", spec)
    write_json(d / "plan.json", plan)
    p = run_ui(["lint", "--spec", "plan.json", "--cases", "spec.json"], d)
    out = (p.stdout + p.stderr).strip()
    check("[CP2-X1] swapped pair exits non-zero", p.returncode != 0, p.returncode)
    check("[CP2-X1] swapped pair prints no traceback", no_traceback(p), out[-300:])
    check("[CP2-X1] swapped pair prints exactly one line", len([ln for ln in out.splitlines() if ln.strip()]) == 1, out)
    check("[CP2-X1] the line names the cases file and says not a test plan", "spec.json" in out and "not a test plan" in out, out)
    check("[CP2-X1] the line says the paths look swapped", "swapped" in out, out)
    check("[CP2-X1] no WARN line", "WARN" not in out, out)
    ok = run_ui(["lint", "--spec", "spec.json", "--cases", "plan.json"], d)
    check("[CP2-X1] the right order still exits 0", ok.returncode == 0, (ok.returncode, ok.stderr))
    lst = d / "list.json"
    write_json(lst, [browser("CP2-T07", APPROVE)])
    pl = run_ui(["lint", "--spec", "spec.json", "--cases", "list.json"], d)
    check("[CP2-X1] a plain list of cases is still a valid plan", pl.returncode == 0, (pl.returncode, pl.stderr))


def tx2_null_cases():
    print("CP2-X2: a cases file that is null or not a plan fails with one line")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    spec = spec_of([state("saving", cases=["CP2-T07"])], vp(1280, 800))
    for tag, payload in (("null", "null"), ("number", "5"), ("no cases list", '{"cases": "x"}'), ("empty object", "{}")):
        d = fresh_dir("nullcases")
        write_json(d / "spec.json", spec)
        (d / "plan.json").write_text(payload)
        p = run_ui(["lint", "--spec", "spec.json", "--cases", "plan.json"], d)
        out = (p.stdout + p.stderr).strip()
        check(f"[CP2-X2] ({tag}) exits non-zero", p.returncode != 0, p.returncode)
        check(f"[CP2-X2] ({tag}) no traceback", no_traceback(p), out[-300:])
        check(f"[CP2-X2] ({tag}) exactly one line naming plan.json and 'not a test plan'",
              len([ln for ln in out.splitlines() if ln.strip()]) == 1 and "plan.json" in out and "not a test plan" in out, out)
        check(f"[CP2-X2] ({tag}) no swapped hint and no WARN", "swapped" not in out and "WARN" not in out, out)
    check("[CP2-X2] capture_gaps itself still returns [] for a null plan", gaps(None, spec) == [], gaps(None, spec))


def tx3_invalid_journey_steps():
    print("CP2-X3: a journey with no valid step is a no-journey gap")
    plan = {"cases": [browser("CP2-T07", APPROVE)]}
    for tag, journey in (("[null]", [None]), ("[{}]", [{}]), ("unknown action", [{"jump": "#x"}]), ("string step", ["click"]),
                         ("empty action", [{"click": ""}])):
        gs = gaps(plan, spec_of([state("saving", journey=journey, cases=["CP2-T07"])], vp(1280, 800)))
        check(f"[CP2-X3] journey {tag} gives one no-journey gap",
              len(gs) == 1 and gs[0].get("missing") == "no-journey", gs)
    mixed = [None, {}, {"click": "#open"}]
    gs = gaps(plan, spec_of([state("saving", journey=mixed, cases=["CP2-T07"])], vp(1280, 800)))
    check("[CP2-X3] a mixed list with one valid step is covered", gs == [], gs)
    for step in ({"goto": "/x"}, {"fill": "#a", "value": "b"}, {"wait": 500}, {"waitFor": "#a"}, {"scroll": "bottom"}):
        gs = gaps(plan, spec_of([state("saving", journey=[step], cases=["CP2-T07"])], vp(1280, 800)))
        check(f"[CP2-X3] step {sorted(step)[0]} is a valid step", gs == [], gs)


TESTS.extend([("CP2-X1", tx1_swapped_paths), ("CP2-X2", tx2_null_cases), ("CP2-X3", tx3_invalid_journey_steps)])


if __name__ == "__main__":
    main()
