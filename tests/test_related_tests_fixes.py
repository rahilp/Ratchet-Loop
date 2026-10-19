#!/usr/bin/env python3
"""
CP3 fix-round tests (adversarial round 1): related tests before the full suite.
    python3 tests/test_related_tests_fixes.py

Black-box like tests/test_related_tests.py (whose fakes and project helpers it reuses; that file is frozen, so
every new case lives here). Temp folders only (_tmp.mkdtemp), the engine's env never this process's, no timing.
  - [CP3-A1-standards]   the setup path turns the related pass on: the app scout and the orchestrator skill name
                         related_test_command, and plan-summary says plainly when the pass is off
  - [CP3-A2]             a related_test_command without {files} is refused by config --set and by complete
  - [CP3-A3-standards]   a related-pass failure saves nothing to state.json (same as a full-suite failure)
  - [CP3-A4]             the refusal shows the runner's own FAIL line and its last line, and names the failing
                         file on its first line, never a passing one
  - [CP3-A1-correctness] a test that imports a changed module (Python dotted path, JS/TS extension-less path)
                         counts as naming it; a bare stem alone does not
  - [CP3-A3-correctness] a test naming a source file the step deleted is related
  - [CP3-A5]             a removed `-- note` or added `++ x` content line is not read as a diff file header
Round 2:
  - [CP3-B2]             the failing file is named on the refusal's first line even when the runner prints only
                         bare file names, as tests/run_all.py does (checked against run_all.py's real output)
  - [CP3-B3-standards]   a related command that writes a file into the project stops the step before the suite
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                          # noqa: E402
import test_gates as g               # noqa: E402
import test_related_tests as rt      # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"), flush=True)


def _engine():
    spec = importlib.util.spec_from_file_location("ratchet_engine_cp3_fixes", str(rt.ENGINE))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _config_json(d) -> dict:
    return json.loads((Path(d) / ".ratchet/config.json").read_text())


# ------------------------------------------------------------------------------------------------ cases

def a1_standards_setup_path():
    print("CP3-A1-standards: the setup path turns the related pass on, and plan-summary says when it is off")
    scout = (ROOT / "agents/ratchet-app-scout.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text(encoding="utf-8")
    check("[CP3-A1-standards] the app scout sets related_test_command from its test_command_template, with {files}",
          "related_test_command" in scout and "{files}" in scout and "test_command_template" in scout)
    scout_line = next((l for l in skill.splitlines() if "ratchet-app-scout" in l and "regression_command" in l), "")
    check("[CP3-A1-standards] the orchestrator skill tells the agent the scout sets related_test_command",
          "related_test_command" in scout_line, scout_line[:200])
    d = rt._project("ps-off", rt.PROJECT_A, ())
    r = rt.rx(d, "plan-summary")
    out = rt._out(r)
    check("[CP3-A1-standards] plan-summary with related_test_command unset says the related pass is off, naming the key",
          r.returncode == 0 and "related_test_command" in out and re.search(r"related pass[^\n]*\boff\b", out), out)
    fk = rt.Fakes("pass")
    d2 = rt._project("ps-on", rt.PROJECT_A, fk.config())
    r2 = rt.rx(d2, "plan-summary")
    check("[CP3-A1-standards] with it configured, plan-summary prints no such note",
          r2.returncode == 0 and "related_test_command is not configured" not in rt._out(r2), rt._out(r2))


def a2_placeholder_required():
    print("CP3-A2: a related_test_command without {files} is refused")
    fk = rt.Fakes("pass")
    d = rt._project("nofiles", rt.PROJECT_A, fk.config())
    r = rt.rx(d, "config", "--set", "related_test_command=pnpm vitest run")
    out = rt._out(r)
    check("[CP3-A2] config --set refuses it, naming related_test_command and {files}",
          r.returncode != 0 and "related_test_command" in out and "{files}" in out, out)
    check("[CP3-A2] the configured value was not changed", _config_json(d).get("related_test_command") == fk.related_cmd,
          _config_json(d).get("related_test_command"))
    r = rt.rx(d, "config", "--set", "related_test_command=null")
    check("[CP3-A2] unsetting it (null) is still accepted", r.returncode == 0 and _config_json(d).get("related_test_command") is None,
          rt._out(r))
    # A value planted by hand (never through config --set) is refused by complete before anything runs.
    fk2 = rt.Fakes("pass")
    d2 = rt._project("nofiles-run", rt.PROJECT_A, fk2.config())
    rt._approve(d2, edits=rt._edits_t01)
    cfg_path = Path(d2) / ".ratchet/config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["related_test_command"] = fk2.related_cmd.replace(" {files}", "")
    cfg_path.write_text(json.dumps(cfg))
    q, ra = rt._approve_changes(d2)
    assert ra.returncode == 0, rt._out(ra)
    head = rt._head(d2)
    r = rt.rx(d2, "complete", "CP1")
    out = rt._out(r)
    check("[CP3-A2] complete is refused, naming related_test_command and {files}",
          r.returncode != 0 and "related_test_command" in out and "{files}" in out, out)
    check("[CP3-A2] neither the related command nor the full suite ran", fk2.calls() == [] and fk2.count() == 0,
          (fk2.calls(), fk2.count()))
    check("[CP3-A2] no flaky allowance was spent and nothing was committed",
          not rt._state(d2).get("regression_flaky_used") and rt._head(d2) == head, (rt._state(d2).get("regression_flaky_used"), head))


def a3_standards_no_state_saved():
    print("CP3-A3-standards: a related-pass failure writes nothing to state.json")
    fk = rt.Fakes("sleep")
    files = dict(rt.PROJECT_A, **{"tests/alpha.test.js": "// checks src/alpha.js\n// BREAK_ME\n"})
    d = rt._project("nosave", files, fk.config())
    rt._approve(d, edits=rt._edits_t01)
    state_path = Path(d) / ".ratchet/runs/f/state.json"
    before = state_path.read_bytes()
    r = rt.rx(d, "complete", "CP1")
    check("[CP3-A3-standards] complete fails on the related pass", r.returncode != 0 and "tests/alpha.test.js" in rt._out(r), rt._out(r))
    check("[CP3-A3-standards] state.json is byte-for-byte what it was before the failed call", state_path.read_bytes() == before)
    check("[CP3-A3-standards] no related_tests_failed entry was written",
          "related_tests_failed" not in state_path.read_text(), state_path.read_text()[-400:])


def a4_failure_output():
    print("CP3-A4: the refusal shows the runner's own output and names the failing file")
    fk = rt.Fakes("sleep")
    files = dict(rt.PROJECT_A, **{"tests/alpha.test.js": "// checks src/alpha.js\n// BREAK_ME\n"})
    d = rt._project("failout", files, fk.config())
    rt._approve(d, edits=rt._edits_t01)
    r = rt.rx(d, "complete", "CP1")
    out = rt._out(r)
    lines = out.splitlines()
    check("[CP3-A4] the fake runner's own 'FAIL tests/alpha.test.js' line is printed", "FAIL tests/alpha.test.js" in lines, out)
    check("[CP3-A4] the runner's last line ('related run: 3 files') is printed", "related run: 3 files" in lines, out)
    first = next((l for l in lines if "cannot complete" in l), "")
    check("[CP3-A4] the refusal's first line names the failing file", "failing: tests/alpha.test.js" in first, first)
    check("[CP3-A4] no line describes the passing planned file as failed",
          not any("tests/cp1.test.js" in l and re.search(r"fail", l, re.I) and "Files run" not in l and "(planned test)" not in l
                  for l in lines), out)
    check("[CP3-A4] the sleeper was never started", not fk.marker.exists() and fk.count() == 0, (fk.marker.exists(), fk.count()))


PROJECT_PY_JS = {
    "src/app/__init__.py": "",
    "src/app/billing.py": "def compute():\n    return 1\n",
    "src/billing.ts": "export const billing = 1;\n",
    "tests/test_invoices.py": "from app.billing import compute\n\ndef test_compute():\n    assert compute() == 1\n",
    "tests/test_ledger.py": "from app import billing\n\ndef test_ledger():\n    assert billing.compute() == 1\n",
    "tests/test_words.py": "# talks about billing in general, imports nothing\n",
    "tests/billing.spec.ts": "import { billing } from '../src/billing';\n",
    "tests/other.spec.ts": "import { x } from './billings';\n// unrelated\n",
}


def _edit_py_js(d):
    (d / "src/app/billing.py").write_text("def compute():\n    return 2\n")
    (d / "src/billing.ts").write_text("export const billing = 2;\n")


def a1_correctness_imports():
    print("CP3-A1-correctness: a test that imports a changed module is related; a bare stem is not")
    fk = rt.Fakes("pass")
    d = rt._project("imports", PROJECT_PY_JS, fk.config())
    rt._approve(d, edits=_edit_py_js)
    r = rt.rx(d, "complete", "CP1")
    out = rt._out(r)
    args = rt._args(fk)
    check("[CP3-A1-correctness] the Python test importing app.billing by dotted path is picked, reason names src/app/billing.py",
          "tests/test_invoices.py" in args and rt._has_reason(out, "tests/test_invoices.py", "names src/app/billing.py"), out)
    check("[CP3-A1-correctness] the Python test using 'from app import billing' is picked",
          "tests/test_ledger.py" in args and rt._has_reason(out, "tests/test_ledger.py", "names src/app/billing.py"), out)
    check("[CP3-A1-correctness] the TS test importing '../src/billing' (no extension) is picked, reason names src/billing.ts",
          "tests/billing.spec.ts" in args and rt._has_reason(out, "tests/billing.spec.ts", "names src/billing.ts"), out)
    check("[CP3-A1-correctness] a test that only uses the bare word, or imports './billings', is not picked",
          "tests/test_words.py" not in args and "tests/other.spec.ts" not in args, args)
    check("[CP3-A1-correctness] the step completes and the full suite ran once", r.returncode == 0 and fk.count() == 1,
          (r.returncode, fk.count()))
    m = _engine()
    check("[CP3-A1-correctness] _names_file: 'import billing' names a top-level billing.py; 'billings' does not",
          m._names_file("import billing\n", "billing.py") and not m._names_file("import billings\n", "billing.py")
          and not m._names_file("the billing rules\n", "billing.py"))
    check("[CP3-A1-correctness] _names_file: the extension-less path stops at a word boundary (src/lonely2.js is not src/lonely)",
          not m._names_file("// checks src/lonely2.js\n", "src/lonely.js") and not m._names_file("app/billing2\n", "src/app/billing.py")
          and m._names_file("import x from '../src/lonely'\n", "src/lonely.js"))
    check("[CP3-A1-correctness] _names_file: a doc file has no import forms (path or file name only)",
          m._names_file("see docs/guide.md\n", "docs/guide.md") and not m._names_file("docs.guide\n", "docs/guide.md"))


def _delete_beta(d):
    (d / "src/beta.js").unlink()
    g.git(d, "add", "-A")


def a3_correctness_deleted_source():
    print("CP3-A3-correctness: a test naming a deleted source file is related")
    fk = rt.Fakes("pass")
    d = rt._project("deleted", rt.PROJECT_A, fk.config())
    rt._approve(d, edits=_delete_beta)
    r = rt.rx(d, "complete", "CP1")
    out = rt._out(r)
    args = rt._args(fk)
    check("[CP3-A3-correctness] tests/beta.test.js (names beta.js) is picked after src/beta.js was deleted",
          "tests/beta.test.js" in args and rt._has_reason(out, "tests/beta.test.js", "names src/beta.js"), out)
    check("[CP3-A3-correctness] the deleted source file itself is not handed to the command", "src/beta.js" not in args, args)
    check("[CP3-A3-correctness] the step completes", r.returncode == 0, out)


def a5_diff_headers():
    print("CP3-A5: '--- '/'+++ ' content lines inside a hunk are not file headers")
    m = _engine()
    d = Path(_tmp.mkdtemp(prefix="pk-hdr-"))
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["config", "diff.noprefix", "true"]):
        g.git(d, *c)
    (d / "src").mkdir()
    (d / "src/q.js").write_text("function a() {\n-- note\nreturn 1\n}\nfunction b() {\nreturn 3\n}\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "base")
    base = g.git(d, "rev-parse", "HEAD").stdout.strip()
    (d / "src/q.js").write_text("function a() {\nreturn 2\n}\nfunction b() {\n++ x\nreturn 4\n}\n")
    g.git(d, "add", "-A")
    g.git(d, "commit", "-qm", "change")
    head = g.git(d, "rev-parse", "HEAD").stdout.strip()
    ctx = types.SimpleNamespace(root=d)
    lines = m._changed_lines(ctx, base, {"id": head}, ["src/q.js"])
    got = lines.get("src/q.js") or {"added": [], "removed": []}
    check("[CP3-A5] the removed '-- note' line and the removed 'return 1' line are both attributed (old lines 2 and 3)",
          got["removed"][:2] == [2, 3] and 6 in got["removed"], lines)
    check("[CP3-A5] the added '++ x' line is content (new line 5), and 'return 4' follows it at line 6",
          (5, "++ x") in got["added"] and (6, "return 4") in got["added"] and (2, "return 2") in got["added"], lines)
    check("[CP3-A5] with diff.noprefix set, the file is still found under its plain path", "src/q.js" in lines and len(lines) == 1,
          list(lines))
    hot = m._changed_function_names(ctx, base, lines)
    check("[CP3-A5] both functions count as changed", hot == {"a", "b"}, hot)


_SCRIPTS2 = Path(_tmp.mkdtemp(prefix="pk-scripts2-"))
# A runner that reports failures by bare file name only, in tests/run_all.py's own format.
_BASENAME_RUNNER = _SCRIPTS2 / "pk_basename.py"
_BASENAME_RUNNER.write_text(
    "import os, sys\n"
    "files = sys.argv[1:]\n"
    "bad = os.path.basename(files[0])\n"
    "print('[1/' + str(len(files)) + '] FAILED (exit 1)  ' + bad)\n"
    "for f in files[1:]:\n"
    "    print('[x/' + str(len(files)) + '] passed  ' + os.path.basename(f))\n"
    "print('Failed: ' + bad)\n"
    "sys.exit(1)\n")
# A runner that passes but writes a file into the project (a snapshot updater).
_WRITING_RUNNER = _SCRIPTS2 / "pk_writer.py"
_WRITING_RUNNER.write_text(
    "import pathlib, sys\n"
    "pathlib.Path('tests/__snap__.txt').write_text('snapshot')\n"
    "print('related run: ' + str(len(sys.argv) - 1) + ' files')\n"
    "sys.exit(0)\n")


def _cmd(script) -> str:
    return f"{rt.shlex.quote(sys.executable)} {rt.shlex.quote(str(script))} {{files}}"


def b2_basename_failure_line():
    print("CP3-B2: the failing file is named even when the runner prints only bare file names")
    fk = rt.Fakes("sleep")
    d = rt._project("basename", rt.PROJECT_A, [f"related_test_command={_cmd(_BASENAME_RUNNER)}", f"regression_command={fk.regression_cmd}"])
    rt._approve(d, edits=rt._edits_t01)
    r = rt.rx(d, "complete", "CP1")
    out = rt._out(r)
    first = next((l for l in out.splitlines() if "cannot complete" in l), "")
    segment = first.split("failing:")[-1].split(". Fix")[0] if "failing:" in first else ""
    check("[CP3-B2] complete fails and its first line names the one failing file (the first argument, tests/cp1.test.js) by its path",
          r.returncode != 0 and re.findall(r"tests/\S+\.test\.js", segment) == ["tests/cp1.test.js"], first)
    check("[CP3-B2] the sleeper was never started", not fk.marker.exists() and fk.count() == 0, (fk.marker.exists(), fk.count()))
    m = _engine()
    base, log = rt._fake_suites(cc_exit=1)
    rr = rt._run_all(base, log, "tests/test_aa.py", "tests/test_cc.py")
    got = m._failing_files(rt._out(rr), ["tests/test_aa.py", "tests/test_cc.py"])
    check("[CP3-B2] fed tests/run_all.py's real output, _failing_files names tests/test_cc.py and not tests/test_aa.py",
          rr.returncode == 1 and got == ["tests/test_cc.py"], (got, rt._out(rr)[-600:]))
    check("[CP3-B2] a bare file name only counts as a whole word (test_cc.py is not test_cc.pyc or my_test_cc.py)",
          m._failing_files("FAILED  test_cc.pyc\nfailed my_test_cc.py\n", ["tests/test_cc.py"]) == []
          and m._failing_files("Failed: test_aa.py, test_cc.py\n", ["tests/test_cc.py"]) == ["tests/test_cc.py"])


def c1_failure_token_not_title():
    print("CP3-C1: only a failure status token marks a failure line; 'fails' in a passing test's title does not")
    m = _engine()
    files = ["tests/auth.test.ts", "tests/test_b.py", "tests/test_c.py"]
    vitest = "✓ tests/auth.test.ts > login fails with a bad password (3ms)\n"
    check("[CP3-C1] a vitest pass line whose title says 'fails' names no file", m._failing_files(vitest, files) == [], vitest)
    pytest_v = "tests/test_b.py::test_login_fails_badly PASSED\n"
    check("[CP3-C1] a pytest -v PASSED line whose test name says 'fails' names no file", m._failing_files(pytest_v, files) == [],
          pytest_v)
    real = "tests/test_b.py::test_login_fails_badly PASSED\nFAILED tests/test_c.py::test_ok - assert 1 == 2\n1 failed, 1 passed\n"
    check("[CP3-C1] a real FAILED line names its file, and only it", m._failing_files(real, files) == ["tests/test_c.py"], real)
    marks = "× tests/auth.test.ts > login\n✓ tests/test_c.py > ok\n"
    check("[CP3-C1] a × mark counts as failure, a ✓ mark as passing", m._failing_files(marks, files) == ["tests/auth.test.ts"], marks)
    run_all = "[1/2] FAILED (exit 1)  test_b.py\n[2/2] passed  test_c.py\nFailed: test_b.py\n"
    check("[CP3-C1] tests/run_all.py's own lines still name the failing suite", m._failing_files(run_all, files) == ["tests/test_b.py"],
          run_all)


def b3_related_command_changes_files():
    print("CP3-B3-standards: a related command that changes project files stops the step before the full suite")
    fk = rt.Fakes("sleep")
    d = rt._project("writer", rt.PROJECT_A, [f"related_test_command={_cmd(_WRITING_RUNNER)}", f"regression_command={fk.regression_cmd}"])
    rt._approve(d, edits=rt._edits_t01)
    head = rt._head(d)
    r = rt.rx(d, "complete", "CP1")
    out = rt._out(r)
    check("[CP3-B3-standards] complete is refused with the 'changed files in the project' message naming the related command",
          r.returncode != 0 and "related test command changed files in the project" in out, out)
    check("[CP3-B3-standards] the full suite (the sleeper) was never started", not fk.marker.exists() and fk.count() == 0,
          (fk.marker.exists(), fk.count()))
    check("[CP3-B3-standards] nothing was committed and the step is not done",
          rt._head(d) == head and rt._cps(d)["status"] != "done", (rt._head(d), head, rt._cps(d).get("status")))


TESTS = [
    ("CP3-B2", b2_basename_failure_line),
    ("CP3-C1", c1_failure_token_not_title),
    ("CP3-B3-standards", b3_related_command_changes_files),
    ("CP3-A1-standards", a1_standards_setup_path),
    ("CP3-A2", a2_placeholder_required),
    ("CP3-A3-standards", a3_standards_no_state_saved),
    ("CP3-A4", a4_failure_output),
    ("CP3-A1-correctness", a1_correctness_imports),
    ("CP3-A3-correctness", a3_correctness_deleted_source),
    ("CP3-A5", a5_diff_headers),
]


def main():
    global FAIL
    only = set(sys.argv[1:])
    for cid, fn in TESTS:
        if only and cid not in only:
            continue
        try:
            fn()
        except Exception as e:  # a crash is a failure for this case, never a silent drop
            FAIL += 1
            print(f"  ✗ [{cid}] CRASHED: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
