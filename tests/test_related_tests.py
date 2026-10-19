#!/usr/bin/env python3
"""
CP3 tests: related tests run before the full suite.   python3 tests/test_related_tests.py

Before the full suite at the end of a step, `ratchet.py complete` runs the few test files related to what the
step changed (its own planned test file, test files the step changed, and test files that name a changed file),
says which ones it picked and why, and stops within seconds, naming the failing file, when one of them breaks,
so the full suite is never started for a step that is already broken. The related list is capped
(`related_tests_max`, default 8) and ranked by the function names that changed, and the files in
`related_always` (Ratchet's own repo: tests/test_docs.py) join the list outside the cap whenever the step adds a
test file, changes a doc, or adds or changes a code line holding a quoted string.

Black-box tests of `ratchet.py complete` in tiny throwaway git projects built the way tests/test_gates.py does
(reusing its `rx`, `git`, `say`, `approve_plan`, `write_tests`, `open_round`, `write_review`). No real test
suite is ever run: `related_test_command` is a tiny fake Python script this file writes, which appends the file
list it was called with (and the regression counter's value at that moment, to prove the order) to a log, and
prints `FAIL <file>` and exits 1 when any file it is handed contains the text BREAK_ME (or is listed in an
outside "break list" file, so a case can break and fix a test without changing the project tree).
`regression_command` is a fake counting script: it increments a counter file and, by mode, passes, fails a set
number of times, always fails, or writes a marker file and sleeps (so a case can prove it was never started).

Temp folder names are neutral (pk-*) and never hold the words the cases look for in Ratchet's output. Nothing
here depends on timing; engine calls get RATCHET_FAKE_LOADAVG=0 so CP2's load stretch never applies.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP3-T01", "status": "passed"|"failed"|"error", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402

ENGINE = g.ENGINE
RUN_ALL = HERE / "run_all.py"
ENV = dict(g.ENV, RATCHET_FAKE_LOADAVG="0")
PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
CASE_ERRORS: dict[str, str] = {}
ID_RE = re.compile(r"\[(CP3-T\d+)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fake commands

SCRIPTS = Path(_tmp.mkdtemp(prefix="pk-scripts-"))

# related_test_command stand-in: argv = log, counter path (or '-'), break-list path (or '-'), then the files.
_RELATED_SRC = (
    "import json, pathlib, sys\n"
    "log, ctr, brk, files = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4:]\n"
    "n = pathlib.Path(ctr).read_text().strip() if ctr != '-' and pathlib.Path(ctr).exists() else '0'\n"
    "with log.open('a') as f:\n"
    "    f.write(json.dumps({'files': files, 'counter': n}) + chr(10))\n"
    "listed = set(pathlib.Path(brk).read_text().split()) if brk != '-' and pathlib.Path(brk).exists() else set()\n"
    "bad = []\n"
    "for f in files:\n"
    "    p = pathlib.Path(f)\n"
    "    if f in listed or (p.exists() and 'BREAK_ME' in p.read_text(errors='replace')):\n"
    "        bad.append(f)\n"
    "for b in bad:\n"
    "    print('FAIL ' + b)\n"
    "print('related run: ' + str(len(files)) + ' files')\n"
    "sys.exit(1 if bad else 0)\n"
)
RELATED_SCRIPT = SCRIPTS / "pk_related.py"
RELATED_SCRIPT.write_text(_RELATED_SRC)

# regression_command stand-in: argv = counter path, mode ('pass', 'fail', 'failfirst:N', 'sleep:<marker path>').
_SUITE_SRC = (
    "import pathlib, sys, time\n"
    "ctr, mode = pathlib.Path(sys.argv[1]), sys.argv[2]\n"
    "n = int(ctr.read_text().strip() or '0') + 1\n"
    "ctr.write_text(str(n))\n"
    "if mode == 'fail':\n"
    "    print('suite failed'); sys.exit(1)\n"
    "if mode.startswith('failfirst:'):\n"
    "    sys.exit(1 if n <= int(mode.split(':', 1)[1]) else 0)\n"
    "if mode.startswith('sleep:'):\n"
    "    pathlib.Path(mode.split(':', 1)[1]).write_text('started')\n"
    "    time.sleep(2)\n"
    "sys.exit(0)\n"
)
SUITE_SCRIPT = SCRIPTS / "pk_suite.py"
SUITE_SCRIPT.write_text(_SUITE_SRC)


class Fakes:
    """One case's fake commands: the related log, the regression counter, the break list and the sleeper marker."""

    def __init__(self, mode="pass"):
        self.dir = Path(_tmp.mkdtemp(prefix="pk-fakes-"))
        self.log = self.dir / "related.log"
        self.counter = self.dir / "counter"
        self.counter.write_text("0")
        self.breaks = self.dir / "breaks.txt"
        self.marker = self.dir / "started.marker"
        self.mode = mode if mode != "sleep" else f"sleep:{self.marker}"

    @property
    def related_cmd(self) -> str:
        return (f"{shlex.quote(sys.executable)} {shlex.quote(str(RELATED_SCRIPT))} {shlex.quote(str(self.log))} "
                f"{shlex.quote(str(self.counter))} {shlex.quote(str(self.breaks))} {{files}}")

    @property
    def regression_cmd(self) -> str:
        return (f"{shlex.quote(sys.executable)} {shlex.quote(str(SUITE_SCRIPT))} {shlex.quote(str(self.counter))} "
                f"{shlex.quote(self.mode)}")

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]

    def count(self) -> int:
        return int(self.counter.read_text().strip() or "0")

    def config(self, related=True, regression=True) -> list[str]:
        out = []
        if related:
            out.append(f"related_test_command={self.related_cmd}")
        if regression:
            out.append(f"regression_command={self.regression_cmd}")
        return out


# ------------------------------------------------------------------------------------------------ projects

PLACEHOLDER = "// placeholder body; the test-writer step rewrites this file\n"

PROJECT_A = {
    "src/alpha.js": "export const alpha = 1;\nexport const rate = 2;\n",
    "src/beta.js": "export const beta = 1;\n",
    "src/orphan.js": "export const orphan = 1;\n",
    "src/shared.js": "export const shared = 1;\n",
    "tests/alpha.test.js": "// checks src/alpha.js\n",
    "tests/beta.test.js": "// checks beta.js by file name only\n",
    "tests/misc.test.js": "// mentions no source file at all\n",
    "README.md": "# Title\n\noriginal sentence.\n",
}
DOC_CHECK = "tests/doc_check.test.js"
PROJECT_A_DOC = dict(PROJECT_A, **{DOC_CHECK: "// wording check; mentions no source file and no doc\n"})


def _shared_js() -> str:
    names = ["parseInvoice", "renderTotal", "applyDiscount", "formatDate", "sumLines", "taxFor", "roundMoney",
             "mergeItems", "splitName", "pickCurrency", "validateId", "normalizeNote"]
    out = []
    for n in names:
        out.append(f"export function {n}(x) {{\n  const a = 1;\n  const b = a + 1;\n  const c = b + 1;\n"
                   f"  const d = c + 1;\n  const e = d + 1;\n  const f = e + 1;\n  return f + x;\n}}\n")
    return "\n".join(out)


def _project_b_files() -> dict:
    files = {"src/shared.js": _shared_js()}
    for i in range(1, 16):
        extra = ""
        if i in (1, 2):
            extra = " and parseInvoice"
        elif i == 3:
            extra = " and renderTotal"
        elif i in (13, 14, 15):
            extra = " and applyDiscount"
        files[f"tests/shared_{i:02d}.test.js"] = f"// checks shared.js{extra}\n"
    return files


def _project_c_files() -> dict:
    files = {"src/mod.js": "export const mod = 1;\n"}
    for i in range(1, 13):
        files[f"tests/mod_{i:02d}.test.js"] = "// checks src/mod.js\n"
    return files


PROJECT_D = {
    "src/alpha.js": "export const alpha = 1;\n",
    "src/beta.js": "export const beta = 1;\n",
    "src/lonely.js": "export const lonely = 1;\n",
    "src/lonely2.js": "export const lonely2 = 1;\n",
    "tests/odd name.test.js": "// checks src/lonely2.js\n",
    "tests/old.test.js": "// checks src/alpha.js\n",
    "tests/beta.test.js": "// checks beta.js\n",
    "tests/misc.test.js": "// mentions no source file\n",
}


def _out(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


def rx(d, *args, env=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True,
                          env=env or ENV)


def _project(name, files, config=(), cps=1, extra_cp=None) -> Path:
    """A throwaway git project whose base commit holds `files` plus a placeholder planned test file
    (tests/cp<N>.test.js) for every checkpoint, so the step later EDITS its own test file, never adds it."""
    d = Path(_tmp.mkdtemp(prefix=f"pk-{name}-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir(exist_ok=True)
    (d / "tests").mkdir(exist_ok=True)
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for i in range(1, cps + 1):
        (d / f"tests/cp{i}.test.js").write_text(PLACEHOLDER)
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    r = rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr
    r = rx(d, "config", "--set", "min_review_seconds=0", "providers=[]", *config)
    assert r.returncode == 0, _out(r)
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": []}
    for i in range(1, cps + 1):
        cp = {"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
              "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
              "tests": {"files": [f"tests/cp{i}.test.js"], "command": f"node --test tests/cp{i}.test.js"}}
        if extra_cp and i in extra_cp:
            cp.update(extra_cp[i])
        plan["checkpoints"].append(cp)
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, _out(r)
    g.approve_plan(d)
    return d


def _approve(d, cp="CP1", edits=None):
    """Drives `cp` through tests-written, red, implemented (after `edits(d)`, the step's own changes), behavior
    and one clean adversarial round, so `complete` is the next step with every gate passed on this exact tree."""
    g.write_tests(d, cp)
    r = rx(d, "tests-written", cp)
    assert r.returncode == 0, _out(r)
    r = rx(d, "red", cp)
    assert r.returncode == 0, _out(r)
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    if edits:
        edits(d)
    r = rx(d, "implemented", cp)
    assert r.returncode == 0, _out(r)
    r = rx(d, "behavior", cp)
    assert r.returncode == 0, _out(r)
    rnd = g.open_round(d, cp)
    path = g.write_review(d, rnd, g.R0(rnd), findings=[])
    r = rx(d, "adv-record", cp, "--reviews", path)
    assert r.returncode == 0, _out(r)


def _edit_alpha(d):
    (d / "src/alpha.js").write_text("export const alpha = 2;\nexport const rate = 2;\n")


def _add_new_check(d):
    (d / "tests/new_check.test.js").write_text("// mentions nothing\n")


def _edits_t01(d):
    _edit_alpha(d)
    _add_new_check(d)


def _approve_changes(d):
    """The user confirms a rule change made after approval (ask, a real user turn, then approve-changes)."""
    q = rx(d, "ask", "approve-changes")
    g.say(d, "yes, go ahead")
    r = rx(d, "approve-changes")
    return q, r


def _state(d, slug="f"):
    return json.loads((Path(d) / f".ratchet/runs/{slug}/state.json").read_text())


def _cps(d, cp="CP1"):
    return _state(d)["checkpoints"][cp]


def _head(d):
    return g.git(d, "rev-parse", "HEAD").stdout.strip()


def _related_log(d, cp="CP1"):
    return Path(d) / f".ratchet/runs/f/evidence/{cp}/related-tests.log"


def _args(fk: Fakes, call=0) -> list[str]:
    calls = fk.calls()
    return calls[call]["files"] if len(calls) > call else []


def _reason_lines(out: str, path: str) -> list[str]:
    return [l for l in out.splitlines() if path in l]


def _has_reason(out: str, path: str, *words) -> bool:
    return any(all(w in l for w in words) for l in _reason_lines(out, path))


def _says_picked(out: str, n: int, totals=(12, 13)) -> bool:
    return any(re.search(rf"\b{n} of {t}\b", out) for t in totals)


_T01 = {}


def _run_t01():
    if "d" not in _T01:
        fk = Fakes("pass")
        d = _project("a", PROJECT_A, fk.config())
        _approve(d, edits=_edits_t01)
        head = _head(d)
        r = rx(d, "complete", "CP1")
        _T01.update(d=d, fk=fk, r=r, head=head)
    return _T01


# ------------------------------------------------------------------------------------------------ cases

def t01():
    t = _run_t01()
    d, fk, r = t["d"], t["fk"], t["r"]
    calls = fk.calls()
    check("[CP3-T01] complete succeeds", r.returncode == 0, _out(r))
    check("[CP3-T01] the related command ran exactly once", len(calls) == 1, calls)
    check("[CP3-T01] it was handed exactly the planned test, the changed test and the test naming src/alpha.js",
          sorted(_args(fk)) == ["tests/alpha.test.js", "tests/cp1.test.js", "tests/new_check.test.js"], _args(fk))
    check("[CP3-T01] the regression counter read 0 when the related command ran (related first)",
          bool(calls) and calls[0]["counter"] == "0", calls)
    check("[CP3-T01] the full suite ran once afterwards", fk.count() == 1, fk.count())
    check("[CP3-T01] the step is done and committed",
          _cps(d)["status"] == "done" and _head(d) != t["head"], (_cps(d).get("status"), _head(d), t["head"]))


def t02():
    t = _run_t01()
    out = _out(t["r"])
    check("[CP3-T02] tests/cp1.test.js is listed as 'planned test'", _has_reason(out, "tests/cp1.test.js", "planned test"), out)
    check("[CP3-T02] tests/new_check.test.js is listed as 'changed test'",
          _has_reason(out, "tests/new_check.test.js", "changed test"), out)
    check("[CP3-T02] tests/alpha.test.js is listed with 'names src/alpha.js'",
          _has_reason(out, "tests/alpha.test.js", "names src/alpha.js"), out)
    check("[CP3-T02] tests/beta.test.js and tests/misc.test.js appear on no line",
          not _reason_lines(out, "tests/beta.test.js") and not _reason_lines(out, "tests/misc.test.js"), out)


_T03 = {}


def _run_t03():
    if "d" not in _T03:
        fk = Fakes("sleep")
        files = dict(PROJECT_A, **{"tests/alpha.test.js": "// checks src/alpha.js\n// BREAK_ME\n"})
        d = _project("a-broken", files, fk.config())
        _approve(d, edits=_edits_t01)
        head = _head(d)
        r = rx(d, "complete", "CP1")
        _T03.update(d=d, fk=fk, r=r, head=head)
    return _T03


def t03():
    t = _run_t03()
    d, fk, r = t["d"], t["fk"], t["r"]
    out = _out(r)
    check("[CP3-T03] complete exits non-zero", r.returncode != 0, out)
    check("[CP3-T03] the message names tests/alpha.test.js and says a related test failed",
          "tests/alpha.test.js" in out and re.search(r"related", out, re.I) and re.search(r"fail", out, re.I), out)
    check("[CP3-T03] the full suite (the sleeper) was never started", not fk.marker.exists() and fk.count() == 0,
          (fk.marker.exists(), fk.count()))
    check("[CP3-T03] the step is not done and nothing was committed",
          _cps(d)["status"] != "done" and _head(d) == t["head"], (_cps(d).get("status"), _head(d), t["head"]))


def t04():
    fk = Fakes("pass")
    d = _project("a-fix", PROJECT_A, fk.config())
    _approve(d, edits=_edits_t01)
    fk.breaks.write_text("tests/alpha.test.js\n")   # broken from outside: the project tree stays exactly as reviewed
    before = {n: dict(_cps(d)["gates"][n]) for n in ("behavior", "adversarial")}
    r1 = rx(d, "complete", "CP1")
    check("[CP3-T04] the first complete fails on the related pass", r1.returncode != 0 and "tests/alpha.test.js" in _out(r1),
          _out(r1))
    check("[CP3-T04] the related command was called once (no automatic retry)", len(fk.calls()) == 1, fk.calls())
    st = _state(d)
    check("[CP3-T04] regression_flaky_used is unset after the related failure", not st.get("regression_flaky_used"), st.get("regression_flaky_used"))
    after = {n: dict(_cps(d)["gates"][n]) for n in ("behavior", "adversarial")}
    check("[CP3-T04] behavior and adversarial gates are still 'passed' with unchanged attempt counts",
          all(after[n]["status"] == "passed" and after[n].get("attempts") == before[n].get("attempts") for n in after),
          (before, after))
    check("[CP3-T04] the full suite never ran for the failed complete", fk.count() == 0, fk.count())
    fk.breaks.write_text("")
    r2 = rx(d, "complete", "CP1")
    check("[CP3-T04] after the fix the second complete passes the related pass and finishes",
          r2.returncode == 0 and _cps(d)["status"] == "done", _out(r2))
    check("[CP3-T04] the related command ran once more and the full suite ran once",
          len(fk.calls()) == 2 and fk.count() == 1, (len(fk.calls()), fk.count()))


def t05():
    t3, t1 = _run_t03(), _run_t01()
    log_fail, log_pass = _related_log(t3["d"]), _related_log(t1["d"])
    check("[CP3-T05] evidence/CP1/related-tests.log exists for the failing run", log_fail.exists(), log_fail)
    check("[CP3-T05] it holds the 'FAIL tests/alpha.test.js' line",
          log_fail.exists() and "FAIL tests/alpha.test.js" in log_fail.read_text(), log_fail.read_text() if log_fail.exists() else "")
    check("[CP3-T05] evidence/CP1/related-tests.log exists for the passing run", log_pass.exists(), log_pass)


def t06():
    fk = Fakes("fail")
    d = _project("a-suitefail", PROJECT_A, fk.config())
    _approve(d, edits=_edits_t01)
    r = rx(d, "complete", "CP1")
    check("[CP3-T06] the related pass ran and passed first", len(fk.calls()) >= 1, fk.calls())
    check("[CP3-T06] the full suite still ran (counter at least 1)", fk.count() >= 1, fk.count())
    check("[CP3-T06] complete exits non-zero with the existing 'Regression suite failed' message",
          r.returncode != 0 and "Regression suite failed" in _out(r), _out(r))
    check("[CP3-T06] the step is not done", _cps(d)["status"] != "done", _cps(d).get("status"))


def t07():
    fk = Fakes("failfirst:1")
    d = _project("a-flaky", PROJECT_A, fk.config())
    _approve(d, edits=_edits_t01)
    r = rx(d, "complete", "CP1")
    check("[CP3-T07] the step finishes", r.returncode == 0 and _cps(d)["status"] == "done", _out(r))
    check("[CP3-T07] it is marked regression_flaky", _cps(d).get("regression_flaky") is True, _cps(d).get("regression_flaky"))
    check("[CP3-T07] the regression counter is 2 (one failure, one retry)", fk.count() == 2, fk.count())
    check("[CP3-T07] the related command was called exactly once (not repeated for the retry)",
          len(fk.calls()) == 1, fk.calls())


def _edit_mod(d):
    (d / "src/mod.js").write_text("export const mod = 2;\n")


def t08():
    fk = Fakes("pass")
    d = _project("c", _project_c_files(), fk.config())
    _approve(d, edits=_edit_mod)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T08] complete succeeds", r.returncode == 0, out)
    check("[CP3-T08] the related command received exactly 8 files in one call",
          len(fk.calls()) == 1 and len(_args(fk)) == 8, fk.calls())
    check("[CP3-T08] the output says it picked 8 of the total (the cap)", _says_picked(out, 8), out)
    check("[CP3-T08] the full suite still ran after it", fk.count() == 1, fk.count())


def _edit_shared(d):
    text = (d / "src/shared.js").read_text()
    parts = text.split("export function ")
    out = []
    for p in parts:
        if p.startswith("parseInvoice(") or p.startswith("renderTotal("):
            p = p.replace("const a = 1;", "const a = 5;")
        out.append(p)
    (d / "src/shared.js").write_text("export function ".join(out))


def t09():
    fk = Fakes("pass")
    d = _project("b", _project_b_files(), fk.config())
    _approve(d, edits=_edit_shared)
    r = rx(d, "complete", "CP1")
    args = _args(fk)
    check("[CP3-T09] complete succeeds", r.returncode == 0, _out(r))
    check("[CP3-T09] the related command received at most 8 files", 0 < len(args) <= 8, args)
    hot = ["tests/shared_01.test.js", "tests/shared_02.test.js", "tests/shared_03.test.js"]
    check("[CP3-T09] the 3 files mentioning a changed function (parseInvoice, renderTotal) are all among them",
          all(h in args for h in hot), args)
    cold = [a for a in args if a.startswith("tests/shared_") and a not in hot]
    check("[CP3-T09] they come before every file that names shared.js but no changed function",
          all(h in args for h in hot) and all(args.index(h) < args.index(c) for h in hot for c in cold), args)
    check("[CP3-T09] the step's own planned test file is among them", "tests/cp1.test.js" in args, args)
    check("[CP3-T09] the other function names in the file (applyDiscount, changed by nobody) pull in no extra files: "
          "the files that mention it are not ranked ahead of the plain ones",
          not any(a in args for a in ("tests/shared_13.test.js", "tests/shared_14.test.js", "tests/shared_15.test.js")), args)


def t10():
    fk = Fakes("pass")
    d = _project("c-cap", _project_c_files(), fk.config() + ["related_tests_max=3"])
    _approve(d, edits=_edit_mod)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    args = _args(fk)
    check("[CP3-T10] the related command received exactly 3 files", len(args) == 3, args)
    check("[CP3-T10] including the step's own planned test file", "tests/cp1.test.js" in args, args)
    check("[CP3-T10] the output says 3 of the total", _says_picked(out, 3), out)
    check("[CP3-T10] the full suite still ran", r.returncode == 0 and fk.count() == 1, (r.returncode, fk.count()))


def t11():
    fk = Fakes("pass")
    d = _project("a-off", PROJECT_A, fk.config(related=False))
    _approve(d, edits=_edits_t01)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T11] the output says the related pass is off and names related_test_command",
          "related_test_command" in out and re.search(r"\b(off|not configured|not set|unset)\b", out, re.I), out)
    check("[CP3-T11] no related log is written", not _related_log(d).exists(), _related_log(d))
    check("[CP3-T11] the regression counter is 1 and the step finishes",
          r.returncode == 0 and fk.count() == 1 and _cps(d)["status"] == "done", (r.returncode, fk.count()))


_T12 = {}


def _run_t12():
    if "d" not in _T12:
        fk = Fakes("pass")
        d = _project("d", PROJECT_D, fk.config())

        def edits(d):
            (d / "src/lonely.js").write_text("export const lonely = 2;\n")
            (d / "src/lonely2.js").write_text("export const lonely2 = 2;\n")
            (d / "src/alpha.js").write_text("export const alpha = 2;\n")
            (d / "tests/old.test.js").unlink()
        _approve(d, edits=edits)
        r = rx(d, "complete", "CP1")
        _T12.update(d=d, fk=fk, r=r)
    return _T12


def t12():
    t = _run_t12()
    args = _args(t["fk"])
    check("[CP3-T12] 'tests/odd name.test.js' reaches the command as one argument",
          "tests/odd name.test.js" in args, args)
    check("[CP3-T12] never split into 'tests/odd' and 'name.test.js'",
          "tests/odd" not in args and "name.test.js" not in args, args)


def t13():
    t = _run_t12()
    args = _args(t["fk"])
    check("[CP3-T13] the deleted tests/old.test.js is not among the arguments",
          bool(args) and "tests/old.test.js" not in args, args)
    check("[CP3-T13] the step completes", t["r"].returncode == 0 and _cps(t["d"])["status"] == "done", _out(t["r"]))


def t14():
    fk = Fakes("pass")
    d = _project("d-lonely", PROJECT_D, fk.config())
    _approve(d, edits=lambda d: (d / "src/lonely.js").write_text("export const lonely = 2;\n"))
    r = rx(d, "complete", "CP1")
    args = _args(fk)
    check("[CP3-T14] the related command received exactly the step's planned test file",
          args == ["tests/cp1.test.js"], args)
    check("[CP3-T14] unchanged tests/misc.test.js and tests/beta.test.js were not given to it",
          "tests/misc.test.js" not in args and "tests/beta.test.js" not in args, args)
    check("[CP3-T14] the step completes", r.returncode == 0, _out(r))


def t15():
    fk = Fakes("pass")
    d = _project("a-stale", PROJECT_A, fk.config())
    _approve(d, edits=_edits_t01)
    (d / "src/alpha.js").write_text("export const alpha = 3;\nexport const rate = 2;\n")
    r = rx(d, "complete", "CP1")
    check("[CP3-T15] complete is refused with the existing 'code changed since behavior passed' message",
          r.returncode != 0 and "code changed since behavior passed" in _out(r), _out(r))
    check("[CP3-T15] the related command was never called", fk.calls() == [], fk.calls())
    check("[CP3-T15] the regression counter is 0", fk.count() == 0, fk.count())


def t16():
    fk = Fakes("pass")
    d = _project("a-rule", PROJECT_A, fk.config())
    _approve(d, edits=_edits_t01)
    always = Fakes("pass")   # a second related command that always exits 0, with its own log
    r = rx(d, "config", "--set", f"related_test_command={always.related_cmd}")
    assert r.returncode == 0, _out(r)
    r1 = rx(d, "complete", "CP1")
    check("[CP3-T16] complete is refused because config rule 'related_test_command' changed since approval",
          r1.returncode != 0 and "config rule 'related_test_command'" in _out(r1), _out(r1))
    check("[CP3-T16] the always-passing command was never run", always.calls() == [], always.calls())
    check("[CP3-T16] the full suite did not run either", fk.count() == 0, fk.count())
    q, ra = _approve_changes(d)
    check("[CP3-T16] approve-changes names the rule and is accepted after the user's reply",
          "related_test_command" in q.stdout and ra.returncode == 0, (q.stdout, _out(ra)))
    r2 = rx(d, "complete", "CP1")
    check("[CP3-T16] after approve-changes the new command is accepted and used",
          r2.returncode == 0 and len(always.calls()) == 1 and fk.calls() == [], (_out(r2), always.calls(), fk.calls()))


DOCS = {
    "README.md": ROOT / "README.md",
    "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
    "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md",
}


def _doc_texts():
    return {k: p.read_text(encoding="utf-8") for k, p in DOCS.items()}


def t17():
    texts = _doc_texts()
    for name, text in texts.items():
        check(f"[CP3-T17] {name} mentions related_test_command", "related_test_command" in text)
        check(f"[CP3-T17] {name} mentions related_tests_max with its default 8",
              bool(re.search(r"related_tests_max[^\n]*\b8\b|\b8\b[^\n]*related_tests_max", text)))
        check(f"[CP3-T17] {name} says the full suite still runs after the related pass",
              bool(re.search(r"related[^\n]*full suite[^\n]*(still|after)|full suite[^\n]*(still|after)[^\n]*related", text, re.I)))
    joined = texts["README.md"] + texts["SKILL.md"]
    check("[CP3-T17] README or SKILL.md names the three reasons a file is picked",
          all(w in joined for w in ("planned test", "changed test")) and re.search(r"names? (a|the) changed file", joined),
          None)


# ---- run_all.py with named suite files

def _fake_suites(cc_exit=0):
    """A temp folder <base>/tests holding test_aa.py, test_bb.py, test_cc.py; each appends its own name to the log
    at $PK_SUITE_LOG. Returns (base, log). run_all.py is called with cwd=base so 'tests/test_aa.py' is both a path
    relative to the working folder and a name inside --dir, the way Ratchet's own related_test_command calls it."""
    base = Path(_tmp.mkdtemp(prefix="pk-suites-"))
    (base / "tests").mkdir()
    log = base / "ran.log"
    for name, code in (("aa", 0), ("bb", 0), ("cc", cc_exit)):
        (base / "tests" / f"test_{name}.py").write_text(
            "import os, pathlib, sys\n"
            f"pathlib.Path(os.environ['PK_SUITE_LOG']).open('a').write('{name}' + chr(10))\n"
            "print('ok')\n"
            f"sys.exit({code})\n")
    return base, log


def _run_all(base, log, *files):
    cache = Path(_tmp.mkdtemp(prefix="pk-cache-"))
    env = dict(os.environ, PK_SUITE_LOG=str(log), XDG_CACHE_HOME=str(cache), RATCHET_FAKE_LOADAVG="0")
    env.pop("RATCHET_RUN_ALL", None)
    env.pop("RATCHET_RUN_ALL_SUITES", None)
    return subprocess.run([sys.executable, str(RUN_ALL), "--dir", str(base / "tests"), "--timeout", "120",
                           "--workers", "2", *files], cwd=base, capture_output=True, text=True, env=env, timeout=300)


def _ran(log):
    return sorted(log.read_text().split()) if log.exists() else []


def t18():
    base, log = _fake_suites()
    r = _run_all(base, log, "tests/test_aa.py", "tests/test_bb.py")
    check("[CP3-T18] exit code 0", r.returncode == 0, _out(r))
    check("[CP3-T18] only aa and bb ran", _ran(log) == ["aa", "bb"], _ran(log))
    check("[CP3-T18] the summary lists 2 suites, not 3", "2 passed, 0 failed" in r.stdout, r.stdout)


def t19():
    base, log = _fake_suites(cc_exit=1)
    r = _run_all(base, log, "tests/test_aa.py", "tests/test_cc.py")
    check("[CP3-T19] exit code 1", r.returncode == 1, (r.returncode, _out(r)))
    check("[CP3-T19] the output names test_cc.py as failed",
          bool(re.search(r"test_cc\.py[^\n]*(failed|FAILED)|(failed|FAILED)[^\n]*test_cc\.py", _out(r))), _out(r))
    check("[CP3-T19] test_bb.py never ran", "bb" not in _ran(log) and "aa" in _ran(log), _ran(log))


def t20():
    base, log = _fake_suites()
    r = _run_all(base, log, "tests/test_aa.py", "tests/test_missing.py")
    check("[CP3-T20] non-zero exit with a plain message naming test_missing.py (not an argument-parser usage error)",
          r.returncode != 0 and "test_missing.py" in _out(r) and "unrecognized arguments" not in _out(r), _out(r))
    check("[CP3-T20] test_aa.py did not run", _ran(log) == [], _ran(log))


def t21():
    base, log = _fake_suites()
    r = _run_all(base, log)
    check("[CP3-T21] all three suites ran with exit code 0", r.returncode == 0 and _ran(log) == ["aa", "bb", "cc"],
          (_out(r), _ran(log)))


def _ignored_cap(out: str) -> bool:
    return "related_tests_max" in out and bool(re.search(r"(ignor|default|fell back|falls back|using 8|used 8|use 8)", out, re.I)) \
        and bool(re.search(r"\b8\b", out))


def t22():
    for value, label in (("0", "0"), ('"many"', "the text 'many'")):
        fk = Fakes("pass")
        d = _project("c-badcap", _project_c_files(), fk.config() + [f"related_tests_max={value}"])
        _approve(d, edits=_edit_mod)
        r = rx(d, "complete", "CP1")
        out = _out(r)
        check(f"[CP3-T22] related_tests_max={label}: the related command received 8 files", len(_args(fk)) == 8, fk.calls())
        check(f"[CP3-T22] related_tests_max={label}: the output says the setting was ignored and 8 was used",
              _ignored_cap(out), out)
        check(f"[CP3-T22] related_tests_max={label}: the step completes", r.returncode == 0, out)


ALWAYS = f'related_always=["{DOC_CHECK}"]'


def t23():
    fk = Fakes("pass")
    d = _project("a-newtest", PROJECT_A_DOC, fk.config() + [ALWAYS])
    _approve(d, edits=_edits_t01)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    args = _args(fk)
    check("[CP3-T23] the related command received tests/doc_check.test.js along with the usual picks",
          DOC_CHECK in args and all(a in args for a in ("tests/cp1.test.js", "tests/new_check.test.js", "tests/alpha.test.js")), args)
    check("[CP3-T23] its reason line names the trigger: 'doc check' and 'new test file'",
          _has_reason(out, DOC_CHECK, "doc check", "new test file"), out)
    check("[CP3-T23] the full suite still ran after", r.returncode == 0 and fk.count() == 1, (r.returncode, fk.count()))


def t24():
    fk = Fakes("pass")
    d = _project("a-docedit", PROJECT_A_DOC, fk.config() + [ALWAYS])

    def edits(d):
        _edit_alpha(d)
        (d / "README.md").write_text("# Title\n\nedited sentence.\n")
    _approve(d, edits=edits)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T24] the related command received tests/doc_check.test.js", DOC_CHECK in _args(fk), fk.calls())
    check("[CP3-T24] its reason line names 'doc check' and 'doc file changed'",
          _has_reason(out, DOC_CHECK, "doc check", "doc file changed"), out)
    check("[CP3-T24] the step completes", r.returncode == 0, out)


def t25():
    fk = Fakes("pass")
    d = _project("a-text", PROJECT_A_DOC, fk.config() + [ALWAYS])
    _approve(d, edits=lambda d: (d / "src/alpha.js").write_text(
        'export const alpha = 1;\nexport const rate = 2;\nexport const msg = "Saved your changes";\n'))
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T25] the related command received tests/doc_check.test.js", DOC_CHECK in _args(fk), fk.calls())
    check("[CP3-T25] its reason line names 'doc check' and 'user-facing text' (or 'string')",
          _has_reason(out, DOC_CHECK, "doc check") and (_has_reason(out, DOC_CHECK, "user-facing text")
                                                       or _has_reason(out, DOC_CHECK, "string")), out)
    check("[CP3-T25] the step completes", r.returncode == 0, out)


def t26():
    fk = Fakes("pass")
    d = _project("a-plain", PROJECT_A_DOC, fk.config() + [ALWAYS])
    _approve(d, edits=lambda d: (d / "src/alpha.js").write_text("export const alpha = 2;\nexport const ratio = 2;\n"))
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T26] the related command ran and the step completes", r.returncode == 0 and len(fk.calls()) == 1, out)
    check("[CP3-T26] tests/doc_check.test.js is not among the arguments", DOC_CHECK not in _args(fk), fk.calls())
    check("[CP3-T26] the output has no 'doc check' line", "doc check" not in out.lower(), out)


def t27():
    fk = Fakes("pass")
    files = dict(PROJECT_A_DOC, **{"src/alpha.js": 'export const alpha = 1;\nexport const label = "old label";\n'})
    d = _project("a-removed", files, fk.config() + [ALWAYS])
    _approve(d, edits=lambda d: (d / "src/alpha.js").write_text("export const alpha = 1;\n"))
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T27] the related command ran and the step completes", r.returncode == 0 and len(fk.calls()) == 1, out)
    check("[CP3-T27] removing a line that held a string literal does not add the doc check",
          DOC_CHECK not in _args(fk) and "doc check" not in out.lower(), (fk.calls(), out))


def t28():
    for cap, expect in ((None, 9), (3, 4)):
        fk = Fakes("pass")
        files = dict(_project_c_files(), **{DOC_CHECK: "// wording check; names nothing\n"})
        cfg = fk.config() + [ALWAYS] + ([f"related_tests_max={cap}"] if cap else [])
        d = _project("c-always", files, cfg)

        def edits(d):
            _edit_mod(d)
            _add_new_check(d)
        _approve(d, edits=edits)
        r = rx(d, "complete", "CP1")
        out = _out(r)
        args = _args(fk)
        label = "default cap" if cap is None else f"related_tests_max={cap}"
        check(f"[CP3-T28] {label}: the related command received exactly {expect} files in one call",
              len(fk.calls()) == 1 and len(args) == expect, fk.calls())
        check(f"[CP3-T28] {label}: tests/doc_check.test.js is among them", DOC_CHECK in args, args)
        if cap is None:
            check("[CP3-T28] default cap: the output says 8 of the total were picked by the cap and the doc check was added on top",
                  _says_picked(out, 8) and "doc check" in out.lower(), out)
        check(f"[CP3-T28] {label}: the step completes", r.returncode == 0, out)


def t29():
    fk = Fakes("pass")
    d = _project("a-noalways", PROJECT_A_DOC, fk.config())

    def edits(d):
        _add_new_check(d)
        (d / "README.md").write_text("# Title\n\nedited sentence.\n")
        (d / "src/alpha.js").write_text('export const alpha = 2;\nexport const rate = 2;\nexport const msg = "Saved";\n')
    _approve(d, edits=edits)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T29] the related command received exactly the T01 set (own, changed, naming)",
          sorted(_args(fk)) == ["tests/alpha.test.js", "tests/cp1.test.js", "tests/new_check.test.js"], fk.calls())
    check("[CP3-T29] no line says 'doc check' and tests/doc_check.test.js was not given to it",
          "doc check" not in out.lower() and DOC_CHECK not in _args(fk), out)
    check("[CP3-T29] the step completes", r.returncode == 0, out)


def t30():
    fk = Fakes("sleep")
    files = dict(PROJECT_A_DOC, **{DOC_CHECK: "// wording check\n// BREAK_ME\n"})
    d = _project("a-docbroken", files, fk.config() + [ALWAYS])
    _approve(d, edits=_edits_t01)
    head = _head(d)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    check("[CP3-T30] complete exits non-zero and names tests/doc_check.test.js", r.returncode != 0 and DOC_CHECK in out, out)
    check("[CP3-T30] the sleeper was never started", not fk.marker.exists() and fk.count() == 0, (fk.marker.exists(), fk.count()))
    check("[CP3-T30] the step is not done and nothing was committed",
          _cps(d)["status"] != "done" and _head(d) == head, (_cps(d).get("status"), _head(d), head))
    check("[CP3-T30] no flaky allowance was spent", not _state(d).get("regression_flaky_used"), _state(d).get("regression_flaky_used"))


def t31():
    fk = Fakes("pass")
    d = _project("a-alwaysrule", PROJECT_A_DOC, fk.config() + [ALWAYS])
    _approve(d, edits=_edits_t01)
    r = rx(d, "config", "--set", "related_always=[]")
    assert r.returncode == 0, _out(r)
    r1 = rx(d, "complete", "CP1")
    check("[CP3-T31] complete is refused because config rule 'related_always' changed since approval",
          r1.returncode != 0 and "config rule 'related_always'" in _out(r1), _out(r1))
    check("[CP3-T31] the weakened list was not used: no related run and no full suite",
          fk.calls() == [] and fk.count() == 0, (fk.calls(), fk.count()))
    q, ra = _approve_changes(d)
    check("[CP3-T31] approve-changes names the rule and is accepted after the user's reply",
          "related_always" in q.stdout and ra.returncode == 0, (q.stdout, _out(ra)))
    r2 = rx(d, "complete", "CP1")
    check("[CP3-T31] after approve-changes the new (empty) list is accepted: the step completes without the doc check",
          r2.returncode == 0 and len(fk.calls()) == 1 and DOC_CHECK not in _args(fk), (_out(r2), fk.calls()))


def t32():
    fk = Fakes("pass")
    d = _project("a-dup", PROJECT_A, fk.config() + ['related_always=["tests/alpha.test.js"]'])
    _approve(d, edits=_edits_t01)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    args = _args(fk)
    check("[CP3-T32] tests/alpha.test.js appears once in the related command's arguments",
          args.count("tests/alpha.test.js") == 1, args)
    lines = [l for l in _reason_lines(out, "tests/alpha.test.js") if "names src/alpha.js" in l or "doc check" in l]
    check("[CP3-T32] it has exactly one reason line, never two", len(lines) == 1, out)
    check("[CP3-T32] the step completes", r.returncode == 0, out)


def t33():
    fk = Fakes("pass")
    d = _project("a-gone", PROJECT_A, fk.config() + ['related_always=["tests/gone.test.js"]'])
    _approve(d, edits=_edits_t01)
    r = rx(d, "complete", "CP1")
    out = _out(r)
    args = _args(fk)
    check("[CP3-T33] the related command is not handed tests/gone.test.js", bool(args) and "tests/gone.test.js" not in args, args)
    check("[CP3-T33] the output says the file named in related_always was not found",
          "tests/gone.test.js" in out and re.search(r"not found|does not exist|missing|no such file", out, re.I), out)
    check("[CP3-T33] the step still runs the rest and the full suite",
          r.returncode == 0 and sorted(args) == ["tests/alpha.test.js", "tests/cp1.test.js", "tests/new_check.test.js"]
          and fk.count() == 1, (r.returncode, args, fk.count()))


def t34():
    d = g.project("fresh")
    r = rx(d, "config")
    cfg = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    check("[CP3-T34] a fresh project's DEFAULT config has related_always as an empty list",
          "related_always" in cfg and cfg["related_always"] == [], cfg.get("related_always", "<missing>"))
    for name, text in _doc_texts().items():
        check(f"[CP3-T34] {name} mentions related_always and its three triggers",
              "related_always" in text and "new test file" in text and "doc file changed" in text
              and re.search(r"user-facing|quoted string|string literal", text) is not None)
        check(f"[CP3-T34] {name} says the always-related files sit outside the cap (on a line about related_always or the doc check)",
              any(("related_always" in l or "doc check" in l) and re.search(r"\bcap\b", l)
                  and re.search(r"outside|on top of|not counted|beyond|does not count|do not count", l, re.I)
                  for l in text.splitlines()))


def _two_step_docs_project(fk: Fakes):
    """tests/test_quick_path_docs.py's t08 fixture, plus related_test_command: a two-checkpoint project whose CP2
    test already exists and is trivially true (approved with --allow-green-first), so CP2's approval lands on
    the exact tree the full suite already passed on when CP1 completed."""
    doc_log = fk.dir / "doc.log"
    doc_script = fk.dir / "doc_test.py"
    doc_script.write_text("import sys, pathlib\n"
                          "pathlib.Path(sys.argv[1]).open('a').write('|'.join(sys.argv[2:]) + chr(10))\n"
                          "sys.exit(0)\n")
    doc_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(doc_script))} {shlex.quote(str(doc_log))} {{files}}"
    files = {
        "src/a.js": "export const a = 1;\n",
        "README.md": "# Title\n\noriginal.\n",
        "tests/readme_check.test.js": "// this suite's name mentions README.md on purpose\n",
        "tests/cp2.test.js": "import t from 'node:test'; import a from 'node:assert';\n"
                             "t('[CP2-T01] already true', () => a.equal(1, 1));\n",
    }
    d = Path(_tmp.mkdtemp(prefix="pk-twostep-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    for rel, text in files.items():
        (d / rel).write_text(text)
    (d / "tests/cp1.test.js").write_text(PLACEHOLDER)
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "init")
    g.say(d, "please build the feature")
    assert rx(d, "init", "feature", "--slug", "f").returncode == 0
    r = rx(d, "config", "--set", "min_review_seconds=0", "providers=[]", f"doc_test_command={doc_cmd}", *fk.config())
    assert r.returncode == 0, _out(r)
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    cp1 = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
           "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
           "tests": {"files": ["tests/cp1.test.js"], "command": "node --test tests/cp1.test.js"}}
    cp2 = dict(cp1, id="CP2", title="Step 2", green_first_ok=True,
               tests={"files": ["tests/cp2.test.js"], "command": "node --test tests/cp2.test.js"})
    plan = {"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp1, cp2]}
    for i in (1, 2):
        (run / "tests" / f"CP{i}.json").write_text(json.dumps({"cases": [{"id": f"CP{i}-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, _out(r)
    g.approve_plan(d)
    return d, doc_log


def _approve_green_first(d, cp="CP2"):
    for args in (("tests-written", cp), ("red", cp, "--allow-green-first", "--reason", "no-op checkpoint, nothing to implement"),
                 ("implemented", cp), ("behavior", cp)):
        r = rx(d, *args)
        assert r.returncode == 0, _out(r)
    rnd = g.open_round(d, cp)
    path = g.write_review(d, rnd, g.R0(rnd), findings=[])
    r = rx(d, "adv-record", cp, "--reviews", path)
    assert r.returncode == 0, _out(r)


def t35():
    fk = Fakes("pass")
    d, doc_log = _two_step_docs_project(fk)
    _approve(d, "CP1")
    r = rx(d, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    check("[CP3-T35] (fixture) the full suite ran once for CP1", fk.count() == 1, fk.count())
    cp1_calls = len(fk.calls())
    check("[CP3-T35] (fixture) the related pass ran for CP1, whose full suite did run", cp1_calls == 1, fk.calls())
    _approve_green_first(d, "CP2")
    (d / "README.md").write_text("# Title\n\nedited after CP2's approval.\n")
    r = rx(d, "complete", "CP2")
    out = _out(r)
    check("[CP3-T35] CP2 completes with the full suite skipped (counter stays 1)", r.returncode == 0 and fk.count() == 1,
          (out, fk.count()))
    check("[CP3-T35] the doc tests ran for the README edit",
          doc_log.exists() and "tests/readme_check.test.js" in doc_log.read_text(), doc_log.read_text() if doc_log.exists() else "")
    check("[CP3-T35] the related command was not called for CP2", len(fk.calls()) == cp1_calls, fk.calls())
    check("[CP3-T35] the output does not say the related pass failed or is off for lack of configuration",
          not re.search(r"related[^\n]*(fail|off|not configured)", out, re.I), out)
    # guard against a stub that never runs the related pass: a CP2 whose full suite does run still calls it
    fk2 = Fakes("pass")
    d2 = _project("twostep-code", PROJECT_A, fk2.config(), cps=2)
    _approve(d2, "CP1", edits=_edits_t01)
    r = rx(d2, "complete", "CP1")
    assert r.returncode == 0, _out(r)
    n1 = len(fk2.calls())
    (d2 / "tests/cp2.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import { f2 } from '../src/f2.js';\n"
        "t('[CP2-T01] works', () => a.equal(f2(), 2));\n")
    for args in (("tests-written", "CP2"), ("red", "CP2")):
        r = rx(d2, *args)
        assert r.returncode == 0, _out(r)
    (d2 / "src/f2.js").write_text("export const f2 = () => 2;\n")
    for args in (("implemented", "CP2"), ("behavior", "CP2")):
        r = rx(d2, *args)
        assert r.returncode == 0, _out(r)
    rnd = g.open_round(d2, "CP2")
    path = g.write_review(d2, rnd, g.R0(rnd), findings=[])
    r = rx(d2, "adv-record", "CP2", "--reviews", path)
    assert r.returncode == 0, _out(r)
    r = rx(d2, "complete", "CP2")
    check("[CP3-T35] a CP2 completion where the full suite does run still calls the related command",
          r.returncode == 0 and fk2.count() == 2 and len(fk2.calls()) == n1 + 1 and "tests/cp2.test.js" in _args(fk2, n1),
          (_out(r), fk2.count(), fk2.calls()))


TESTS = [(f"CP3-T{i:02d}", fn) for i, fn in enumerate(
    [t01, t02, t03, t04, t05, t06, t07, t08, t09, t10, t11, t12, t13, t14, t15, t16, t17, t18, t19, t20, t21, t22,
     t23, t24, t25, t26, t27, t28, t29, t30, t31, t32, t33, t34, t35], 1)]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid, _ in TESTS:
        if cid in CASE_ERRORS:
            cases.append({"id": cid, "status": "error", "message": CASE_ERRORS[cid][:300]})
        elif CASE_STATUS.get(cid, False):
            cases.append({"id": cid, "status": "passed", "message": ""})
        else:
            cases.append({"id": cid, "status": "failed", "message": "; ".join(CASE_MESSAGES.get(cid, []))[:300]})
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    only = set(sys.argv[1:])
    for cid, fn in TESTS:
        if only and cid not in only and cid[-2:].lstrip("0") not in only:
            continue
        CASE_STATUS.setdefault(cid, True)
        print(f"{cid}")
        try:
            fn()
        except Exception as e:  # a crash is reported as an error for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_ERRORS[cid] = f"crashed: {e}"
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
