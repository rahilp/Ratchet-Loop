#!/usr/bin/env python3
"""
CP7 tests: the docs, the timeout message and the code comments say what Ratchet actually does.
    python3 tests/test_docs_accuracy.py

  - [CP7-T01..T04]  the adversarial-loop SKILL.md, the orchestrator SKILL.md and agents/ratchet-adversary.md say a
                    review that leaves files out of a fix round's diff is refused, and none says the 'too large'
                    fallback never applies to a scoped (fix) round
  - [CP7-T05..T08]  black-box, the real `ratchet.py behavior` in a throwaway git project (the helpers of
                    tests/test_load_timeouts.py): a command killed by its time limit is reported as timed out even
                    when it also changed a tracked file, with the accounting of a plain timeout; a command that ends
                    on its own after changing a file is still the uncounted refusal. The watch interval is set above
                    the time limit so the periodic look never fires and the change is only seen by the last look;
                    the engine alone sees an idle machine (RATCHET_FAKE_LOADAVG=0)
  - [CP7-T09..T11]  the _Watch and _run_cmd docstrings name the current rules and no removed one
  - [CP7-T12..T14]  the case lists at the top of the two fix-round test files cover every id their bodies run

Temp folders only (_tmp.mkdtemp), everything a scenario starts is killed, the environment is restored.
Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402,F401
import trust_helpers as tt  # noqa: E402
import test_load_timeouts as lt  # noqa: E402  (work, project_n, to_red, rx, reap, evidence, gate, read, fake)

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
CASE_ERRORS: dict = {}
ID_RE = re.compile(r"\[(CP7-T\d+)\]")
lt.check = lambda *a, **k: check(*a, **k)   # assertions made inside borrowed helpers count here

DOCS = {
    "skills/ratchet-adversarial-loop/SKILL.md": ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md",
    "skills/ratchet-orchestrator/SKILL.md": ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md",
    "agents/ratchet-adversary.md": ROOT / "agents" / "ratchet-adversary.md",
}
ENGINE = tt.ENGINE


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:160]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


# ---- doc helpers --------------------------------------------------------------------------------------------------

def read(p) -> str:
    return Path(p).read_text(encoding="utf-8", errors="replace")


def sentences(text: str) -> list:
    flat = re.sub(r"\s+", " ", text)
    return [s for s in re.split(r"(?<=[.!?])\s+", flat) if s]


OMIT_RE = re.compile(r"leav\w+ (?:[\w'`-]+ ){0,6}out|left out|omit|never saw", re.I)
FIXROUND_RE = re.compile(r"fix round|fixer|fix's diff|fix that", re.I)


def omitted_refusal(text: str) -> list:
    """Sentences saying a review that leaves files of a fix round's diff out is refused."""
    return [s for s in sentences(text) if re.search(r"refus", s, re.I) and OMIT_RE.search(s) and FIXROUND_RE.search(s)]


def never_applies(text: str) -> list:
    """Sentences pairing the 'too large' fallback with 'never' and a scoped or fix round."""
    return [s for s in sentences(text)
            if re.search(r"too large", s, re.I) and re.search(r"\bnever\b", s, re.I)
            and re.search(r"scoped round|fix round", s, re.I)
            and re.search(r"fallback|applies", s, re.I)]


# ---- CP7-T01..T04 -------------------------------------------------------------------------------------------------

def doc_case(cid, rel):
    text = read(DOCS[rel])
    check(f"[{cid}] {rel} says a review that leaves files out of a fix round's diff is refused",
          bool(omitted_refusal(text)), "no sentence with refus + omitted/left out + fix round")
    check(f"[{cid}] {rel} does not say the 'too large' fallback never applies to a scoped round",
          not never_applies(text), never_applies(text)[:1])


def t01():
    doc_case("CP7-T01", "skills/ratchet-adversarial-loop/SKILL.md")


def t02():
    doc_case("CP7-T02", "skills/ratchet-orchestrator/SKILL.md")


def t03():
    doc_case("CP7-T03", "agents/ratchet-adversary.md")


def t04():
    bad = [rel for rel, p in DOCS.items() if never_applies(read(p))]
    check("[CP7-T04] no document has a sentence saying the too-large fallback never applies to a scoped round",
          not bad, f"still in: {bad}")
    # the detector itself can fire (a synthetic sentence of the old kind is found)
    old = 'The "too large" fallback above never applies to a scoped round\'s own diff.'
    check("[CP7-T04] the detector finds the old claim in a synthetic sentence", bool(never_applies(old)), old)
    check("[CP7-T04] the detector ignores the unrelated command-line-prompt sentence",
          not never_applies("A prompt too large for a command line never benches the provider."), "")


# ---- CP7-T05..T08: the real behavior command ----------------------------------------------------------------------

SCRIPT = r'''
import os, signal, subprocess, sys, time
for name in ("SIGTERM", "SIGINT", "SIGHUP"):
    sig = getattr(signal, name, None)
    if sig is not None:
        signal.signal(sig, signal.SIG_DFL)
W = {w!r}
mode = open(os.path.join(W, "mode")).read().strip()
if mode == "fail":
    print("not implemented yet")
    sys.exit(1)
if mode == "pass":
    sys.exit(0)
if mode in ("hang_write", "write_exit"):
    with open("src/a.js", "a") as f:
        f.write("// regenerated %f\n" % time.time())
open(os.path.join(W, "script.pid"), "w").write(str(os.getpid()))
if mode == "write_exit":
    sys.exit(0)
time.sleep(300)
'''


def scenario(kind):
    """A project at the green step, then `behavior CP1` run for real with `kind` as the command's behavior."""
    w = lt.work(kind)
    script = w / "cmd.py"
    script.write_text(SCRIPT.format(w=str(w)))
    cmd = f"{lt.PY} {script}"
    env = tt.make_env("claude")
    idle = lt.fake(env, "0", "4")
    d = lt.project_n(env, 1, cmd, ["test_timeout_sec=2", "suite_watch_seconds=30"])

    def mode(m):
        (w / "mode").write_text(m)

    try:
        mode("fail")
        lt.to_red(d, env)
        r = tt.rx(d, idle, "red", "CP1")
        assert r.returncode == 0, r.stderr + r.stdout
        mode("pass")
        (d / "src/f1.js").write_text("export const f = () => 1;\n")
        r = tt.rx(d, idle, "implemented", "CP1")
        assert r.returncode == 0, r.stderr + r.stdout
        before = snapshot(d)
        mode(kind)
        run = lt.rx(d, idle, "behavior", "CP1", cap=90)
    finally:
        lt.reap(w)
    return d, before, run


def snapshot(d):
    g = lt.gate(d)
    return {k: g.get(k) for k in ("attempts", "flaky_used", "runs")}


_CACHE: dict = {}


def once(kind):
    if kind not in _CACHE:
        _CACHE[kind] = scenario(kind)
    return _CACHE[kind]


def blog(d):
    return lt.read(lt.evidence(d) / "behavior-01.log")


def t05():
    d, before, r = once("hang_write")
    log = blog(d)
    check("[CP7-T05] the call ended (not hung) with a failure", r.rc not in (None, 0), (r.rc, r.text[-300:]))
    check("[CP7-T05] the output says TIMEOUT after", "TIMEOUT after" in r.text, r.text[-400:])
    check("[CP7-T05] the kept log carries TIMEOUT after", "TIMEOUT after" in log, log[:400])
    for phrase in ("ended on its own", "found when it ended"):
        check(f"[CP7-T05] the output does not say '{phrase}'", phrase not in r.text, r.text[-400:])
        check(f"[CP7-T05] the log does not say '{phrase}'", phrase not in log, log[:400])
    check("[CP7-T05] the log has no '# code changed under the run' header", "# code changed under the run" not in log,
          log[:400])


def t06():
    d, before, r = once("hang_write")
    _, pbefore, _ = once("hang_plain")
    pd, _, _ = once("hang_plain")
    after, pafter = snapshot(d), snapshot(pd)
    check("[CP7-T06] both projects start from the same accounting", before == pbefore, (before, pbefore))
    check("[CP7-T06] a plain timeout does move the accounting (attempts went up)",
          (pafter.get("attempts") or 0) > (pbefore.get("attempts") or 0), (pbefore, pafter))
    check("[CP7-T06] attempts, flaky_used and runs end exactly as for a plain timeout with no file change",
          after == pafter, (after, pafter))
    check("[CP7-T06] no stopped-runs.json entry is written for the command",
          not (lt.evidence(d) / "stopped-runs.json").exists()
          or "cmd.py" not in lt.read(lt.evidence(d) / "stopped-runs.json"),
          lt.read(lt.evidence(d) / "stopped-runs.json"))


def t07():
    d, before, r = once("write_exit")
    log = blog(d)
    check("[CP7-T07] the call is refused", r.rc not in (None, 0), (r.rc, r.text[-300:]))
    check("[CP7-T07] the log says 'found when it ended'", "found when it ended" in log, log[:400])
    check("[CP7-T07] there is no TIMEOUT marker in the log or the output", "TIMEOUT" not in log and "TIMEOUT" not in r.text,
          log[:300])
    check("[CP7-T07] attempts (and flaky_used, runs) did not change", snapshot(d) == before, (before, snapshot(d)))


def t08():
    d, before, r = once("hang_plain")
    log = blog(d)
    check("[CP7-T08] the TIMEOUT marker is in the log and the output", "TIMEOUT after" in log and "TIMEOUT after" in r.text,
          (log[:300], r.text[-300:]))
    check("[CP7-T08] no 'code changed' wording in the log or the output",
          "code changed" not in log.lower() and "code changed" not in r.text.lower(), r.text[-400:])
    wd, _, _ = once("hang_write")
    check("[CP7-T08] the accounting equals the one of the timeout with a file change", snapshot(d) == snapshot(wd),
          (snapshot(d), snapshot(wd)))


# ---- CP7-T09..T11: the docstrings ---------------------------------------------------------------------------------

_RT = None


def engine_module():
    global _RT
    if _RT is None:
        spec = importlib.util.spec_from_file_location("ratchet_engine_docs_accuracy", str(ENGINE))
        _RT = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _RT
        spec.loader.exec_module(_RT)
    return _RT


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "")


def t09():
    doc = squash(engine_module()._Watch.__doc__)
    low = doc.lower()
    check("[CP7-T09] the removed rule 'source extension outside every build' is gone",
          "source extension outside every build" not in low, doc[:300])
    check("[CP7-T09] it says any added file git would commit counts as new source",
          re.search(r"git would commit", low) is not None, doc[:400])
    check("[CP7-T09] it names the fixed generated or doc list that exempts files",
          re.search(r"generated", low) is not None and re.search(r"\bdoc", low) is not None, doc[:400])
    check("[CP7-T09] it says report folders are not generated",
          re.search(r"report[^.]{0,60}not[^.]{0,30}generated", low) is not None, doc[:400])
    check("[CP7-T09] it names the one kept index of periodic looks",
          re.search(r"index[^.]{0,40}kept|kept[^.]{0,40}index", low) is not None, doc[:400])
    check("[CP7-T09] it names `skipped` for a skipped look", "`skipped`" in doc, doc[:400])
    check("[CP7-T09] it says the last look is the full fingerprint", "full fingerprint" in low, doc[:400])
    check("[CP7-T09] it names found_at_end", "found_at_end" in doc, doc[:400])
    check("[CP7-T09] it says only edited or deleted started files plus new project source count",
          re.search(r"edited or deleted", low) is not None and re.search(r"new project source", low) is not None, doc[:400])


def t10():
    doc = squash(engine_module()._run_cmd.__doc__)
    low = doc.lower()
    check("[CP7-T10] the removed phrase 'delete or ignore it' is gone", "delete or ignore it" not in low, doc[-500:])
    check("[CP7-T10] it says every named file is restored from the run's starting tree",
          re.search(r"restor\w+[^.]{0,80}(start\w* tree|tree the run started|run's start)", low) is not None, doc[-600:])
    check("[CP7-T10] it says a file the run started with is never deleted",
          re.search(r"never (be )?delet", low) is not None, doc[-600:])
    check("[CP7-T10] it names the stretched limit rules (highest reading wins, never below test_timeout_sec, "
          "capped by load_stretch_max)",
          "highest reading" in low and re.search(r"never below `?test_timeout_sec|never below `timeout`", low) is not None
          and "load_stretch_max" in doc, doc[:700])
    check("[CP7-T10] it names `TIMEOUT after <limit>s` with exit 124",
          "TIMEOUT after <limit>s" in doc and "124" in doc, doc[:700])
    check("[CP7-T10] it says the stop on a code change changes no attempt or flaky allowance",
          re.search(r"no attempt", low) is not None and "flaky" in low, doc[:900])
    check("[CP7-T10] it says a kill by the time limit is reported as a timeout even when the tree also changed",
          any(re.search(r"time limit|timeout|timed out", s, re.I) and re.search(r"even (when|if)|also changed", s, re.I)
              and re.search(r"chang", s, re.I) for s in sentences(doc)), doc[-700:])


def strip_docstrings(src: str, names: tuple) -> str:
    tree = ast.parse(src)
    lines = src.splitlines()
    drop = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                drop.update(range(first.lineno - 1, first.end_lineno))
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


def t11():
    rt = engine_module()
    docs = squash(rt._Watch.__doc__) + " " + squash(rt._run_cmd.__doc__)
    code = strip_docstrings(read(ENGINE), ("_Watch", "_run_cmd"))
    for ident in ("stopped-runs.json", "stopped: code changed", "found_at_end", "RATCHET_TEST_REPORT",
                  "load_stretch_max", "suite_watch_seconds", "TIMEOUT after"):
        check(f"[CP7-T11] the docstrings name '{ident}'", ident in docs, docs[:200])
        check(f"[CP7-T11] '{ident}' exists in the module source outside the two docstrings", ident in code, ident)


# ---- CP7-T12..T14: the case lists ---------------------------------------------------------------------------------

def missing_ids(text: str, prefix: str) -> list:
    """Ids [<prefix>-Xnn] run in the body of a test file that its opening docstring does not list."""
    m = re.match(r'(?s)\s*(?:#![^\n]*\n)?\s*(?:r|u)?"""(.*?)"""', text)
    if not m:
        return ["<no opening docstring>"]
    head, body = m.group(1), text[m.end():]
    listed = set(re.findall(rf"\[({re.escape(prefix)}-[A-Z]\d+)\]", head))
    rng = re.compile(rf"\[?(?:{re.escape(prefix)}-)?([A-Z])(\d+)\]?\s*(?:to|through|-|\.\.|–)\s*\[?(?:{re.escape(prefix)}-)?\1(\d+)\]?")
    for mm in rng.finditer(head):
        for n in range(int(mm.group(2)), int(mm.group(3)) + 1):
            listed.add(f"{prefix}-{mm.group(1)}{n:02d}" if len(mm.group(2)) > 1 else f"{prefix}-{mm.group(1)}{n}")
    used = set(re.findall(rf"\[({re.escape(prefix)}-[A-Z]\d+)\]", body))
    return sorted(used - listed)


def t12():
    miss = missing_ids(read(HERE / "test_stop_on_change_fixes.py"), "CP4")
    check("[CP7-T12] every CP4 id the body runs is listed in the docstring (A7 included)", not miss,
          f"missing: {miss}")


def t13():
    miss = missing_ids(read(HERE / "test_load_timeouts_fixes.py"), "CP2")
    check("[CP7-T13] every CP2 id the body runs is listed in the docstring (X01 to X05 included)", not miss,
          f"missing: {miss}")


def t14():
    synth = '"""\nCases:\n  - [CP4-A1] one\n  - [CP4-A2] two\n  - [CP4-A3] three\n"""\nx = "[CP4-A4] four"\n' \
            'y = "[CP4-A1] again"\n'
    check("[CP7-T14] the comparison reports A4 as missing from a synthetic file", missing_ids(synth, "CP4") == ["CP4-A4"],
          missing_ids(synth, "CP4"))
    fixed = synth.replace("  - [CP4-A3] three", "  - [CP4-A3] three\n  - [CP4-A4] four")
    check("[CP7-T14] listing A4 makes it pass (the check is not always failing)", missing_ids(fixed, "CP4") == [],
          missing_ids(fixed, "CP4"))
    ranged = synth.replace("[CP4-A1] one\n  - [CP4-A2] two\n  - [CP4-A3] three", "A1 to A4 all listed")
    check("[CP7-T14] a range 'A1 to A4' in the docstring covers A4", missing_ids(ranged, "CP4") == [],
          missing_ids(ranged, "CP4"))


TESTS = [("CP7-T01", t01), ("CP7-T02", t02), ("CP7-T03", t03), ("CP7-T04", t04), ("CP7-T05", t05), ("CP7-T06", t06),
         ("CP7-T07", t07), ("CP7-T08", t08), ("CP7-T09", t09), ("CP7-T10", t10), ("CP7-T11", t11), ("CP7-T12", t12),
         ("CP7-T13", t13), ("CP7-T14", t14)]


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
        if only and cid not in only and fn.__name__ not in only:
            continue
        CASE_STATUS.setdefault(cid, True)
        print(f"{cid} {fn.__name__}", flush=True)
        try:
            fn()
        except BaseException as e:  # a crash is reported as an error for this case, never a silent drop
            if isinstance(e, KeyboardInterrupt):
                raise
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_ERRORS[cid] = f"crashed: {e}"
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    for d in lt._PIDFILES:
        lt.reap(d)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
