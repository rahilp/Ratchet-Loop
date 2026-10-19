#!/usr/bin/env python3
"""
CP6 extra (structural) test for the shortcut table.   python3 tests/test_shortcuts_extra.py

One rule for every path: each row of a table shows either a real verdict from the right version of the code or a
visible failure (errored, unknown, a table that could not run). Nothing silently looks better than it is, and
nothing disappears. The test walks every path (clean start, after merged, a clean merge with an unrelated hand-
resolved conflict, an unknown old side, a table edited or removed during the run, an unreadable table, a failed
build, a runner that uses relative paths, a table made during the run, a NUL byte, a skipped table) and checks
each against an independent oracle that evaluates the toy guard text itself.

RATCHET_TEST_ENGINE=<path> runs the whole walk against another copy of ratchet.py (used to prove this test fails
on the code before the fixes).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_gates as g           # noqa: E402
import test_dm_finish as df      # noqa: E402
import test_shortcuts as ts      # noqa: E402

if os.environ.get("RATCHET_TEST_ENGINE"):
    g.ENGINE = Path(os.environ["RATCHET_TEST_ENGINE"])

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))
    if not cond:
        FAILS.append(name)


def guard_says(text, cmd):
    ns: dict = {}
    exec(compile(text, "guard.py", "exec"), ns)
    return "REFUSED" if any(r(cmd) for r in ns["RULES"]) else "ALLOWED"


def oracle(expect, old_text, new_text, cmd):
    want = {"refused": "REFUSED", "allowed": "ALLOWED"}[expect]
    old, new = guard_says(old_text, cmd), guard_says(new_text, cmd)
    if old == want:
        return "held" if new == want else "worse"
    return "fixed" if new == want else "still_open"


def rows_ok(tag, j, base_rows, old_text, new_text, name="toy", exact=True):
    """Every starting row is present with its starting expectation; its verdict is the real one, or (only when
    `exact` is False) a visible failure (unknown or errored), never a better one."""
    t = ts.tbl(j, name)
    check(f"{tag}: table {name} is in the output", t is not None and t.get("ran") is True, t)
    rows = ts.rows_of(j, name)
    for b in base_rows:
        r = rows.get(b["id"])
        check(f"{tag}: row {b['id']} is shown", r is not None, sorted(rows))
        if r is None:
            continue
        check(f"{tag}: row {b['id']} keeps its starting expectation", r.get("expect") == b["expect"], r)
        real = oracle(b["expect"], old_text, new_text, b["command"])
        v = r.get("verdict")
        ok = v == real or (not exact and v in ("unknown", "errored"))
        check(f"{tag}: row {b['id']} is {real}" + ("" if exact else " or a visible failure"), ok, r)
        if v == "unknown":
            check(f"{tag}: row {b['id']} unknown means the old side is unknown", r.get("old") == "unknown", r)


def new_guard(d):
    return (Path(d) / "guard.py").read_text()


def edit_table(d, fn):
    p = Path(d) / "tests/shortcuts/toy.json"
    t = json.loads(p.read_text())
    fn(t)
    p.write_text(json.dumps(t, indent=1) + "\n")


def drop_and_flip(t):
    t["rows"] = [r for r in t["rows"] if r["id"] != "tee"]
    for r in t["rows"]:
        if r["id"] == "cat":
            r["expect"] = "refused"


RUNNER_REL = ts.RUNNER.replace('os.path.join(root, "guard.py")', '"guard.py"').replace(
    'os.path.join(root, "written_by_runner.txt")', '"written_by_runner.txt"')
assert RUNNER_REL != ts.RUNNER


def unrelated_conflict_project(name):
    """The lane and main both edit notes.txt (resolved by hand) while the guard merges cleanly."""
    d = df._approved(name, steps=ts.MSTEPS, dm=df.DM3, branch="lane",
                     extra_files=ts.toy_files(extra={"notes.txt": "base\n"}))
    start = g.git(d, "rev-parse", "HEAD").stdout.strip()
    (d / "guard.py").write_text(ts.GUARD_LANE)
    (d / "notes.txt").write_text("lane\n")
    code1 = [c for c in ts.MSTEPS[0] if c.get("layer") == "code"]
    r = df._build(d, 1, code1, report=ts.MPASS1)
    assert r.returncode == 0, ts.out_of(r)
    g.git(d, "checkout", "-q", "-b", "main", start)
    (d / "guard.py").write_text(ts.GUARD_MAIN)
    (d / "notes.txt").write_text("main\n")
    ts._commit(d, "main change", "guard.py", "notes.txt")
    g.git(d, "checkout", "-q", "lane")
    r = g.git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    assert r.returncode != 0, "expected a merge conflict in notes.txt"
    (d / "notes.txt").write_text("lane and main\n")
    g.git(d, "add", "-A")
    r = g.git(d, "commit", "-qm", "Merge main into lane (resolved by hand)")
    assert r.returncode == 0, r.stderr + r.stdout
    r = g.rx(d, "merged")
    assert r.returncode == 0, ts.out_of(r)
    return d


def load_engine():
    spec = importlib.util.spec_from_file_location("ratchet_engine_under_test", str(g.ENGINE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def walk():
    B = ts.BASE
    print("[clean start]")
    d = ts.toy_a("x-clean")
    r, j = ts.scj(d)
    check("clean: exits 0", ts.clean(r), ts.out_of(r)[-300:])
    rows_ok("clean", j, B, ts.GUARD_START, new_guard(d))

    print("[after merged]")
    d, _ = ts.m_project("x-merged", "M")
    r, j = ts.scj(d)
    rows_ok("merged", j, B, ts.GUARD_MAIN, new_guard(d))

    print("[clean merge with an unrelated hand-resolved conflict]")
    d = unrelated_conflict_project("x-unrel")
    r, j = ts.scj(d)
    rows_ok("unrelated conflict", j, B, ts.GUARD_MAIN, new_guard(d))

    print("[unknown old side: merged with no starting record]")
    d, _ = ts.m_project("x-unknown", "M", strip_run_start=True)
    r, j = ts.scj(d)
    check("unknown: exits 0", ts.clean(r), ts.out_of(r)[-300:])
    rows_ok("unknown", j, B, ts.GUARD_MAIN, new_guard(d), exact=False)

    print("[conflict on the watched file, and the table edited during the run]")
    d, _ = ts.m_project("x-conf-edit", "M3")
    edit_table(d, drop_and_flip)
    r, j = ts.scj(d)
    check("conflict+edit: exits 0", ts.clean(r), ts.out_of(r)[-300:])
    rows_ok("conflict+edit", j, B, ts.GUARD_MAIN3, new_guard(d), exact=False)

    print("[table edited during the run]")
    d = ts.toy_a("x-edit")
    edit_table(d, drop_and_flip)
    r, j = ts.scj(d)
    rows_ok("edited", j, B, ts.GUARD_START, new_guard(d))

    print("[table removed during the run]")
    d = ts.toy_a("x-removed")
    (d / "tests/shortcuts/toy.json").unlink()
    r, j = ts.scj(d)
    rows_ok("removed", j, B, ts.GUARD_START, new_guard(d))

    print("[unreadable table that existed at the start]")
    d = ts.toy_a("x-unreadable")
    (d / "tests/shortcuts/toy.json").write_text("{not json")
    r, j = ts.scj(d)
    rows_ok("unreadable (starting copy)", j, B, ts.GUARD_START, new_guard(d))

    print("[a runner that finds the code through its working folder]")
    d = ts.toy_a("x-rel", extra={"tests/shortcuts/toy_runner.py": RUNNER_REL})
    r, j = ts.scj(d)
    rows_ok("relative runner", j, B, ts.GUARD_START, new_guard(d))

    print("[a table made during the run, runner writes relative to its folder]")
    files = ts.toy_files(extra={"tests/shortcuts/toy_runner.py": RUNNER_REL})
    del files["tests/shortcuts/toy.json"]
    d = df._approved("x-newtable", steps=df.FIN2_STEPS, dm=df.DM2, extra_files=files)
    (d / "guard.py").write_text(ts.GUARD_NEW)
    write_row = {"id": "write", "command": "WRITE", "expect": "allowed"}
    (d / "tests/shortcuts/new.json").write_text(ts.table_json(B + [write_row], name="new"))
    r, j = ts.scj(d)
    rows_ok("new table", j, B + [write_row], ts.GUARD_START, new_guard(d), name="new")
    check("new table: the runner's write did not land in the project", not (d / "written_by_runner.txt").exists())

    print("[a NUL byte in a row command]")
    nul = {"id": "nul", "command": "a\x00b", "expect": "refused"}
    d = ts.toy_a("x-nul", rows=B + [nul])
    r, j = ts.scj(d)
    check("nul: exits 0 without a traceback", ts.clean(r) and j is not None, ts.out_of(r)[-300:])
    rows_ok("nul", j, B, ts.GUARD_START, new_guard(d))
    check("nul: the NUL row is errored", ts.rows_of(j).get("nul", {}).get("verdict") == "errored", ts.rows_of(j).get("nul"))

    print("[a new unreadable table is a visible failure through the final review]")
    d = ts.toy_a("x-badnew")
    (d / "tests/shortcuts/bad.json").write_text("{not json")
    r, j = ts.scj(d)
    bad = ts.tbl(j, "bad")
    check("bad table: shown with failed true", bad is not None and bad.get("failed") is True and not bad.get("ran"), bad)
    fin = df._build(d, 1, df.FIN2_STEPS[0], report=df.PASSED2)
    check("bad table: the finish prints that it could not run", "could not run" in ts.out_of(fin), ts.out_of(fin)[-600:])
    sj = ts.sjson(d)
    names = {t.get("name"): t for t in (sj.get("shortcuts") or {}).get("tables") or []}
    check("bad table: status --json still carries it as failed", bool((names.get("bad") or {}).get("failed")), names)
    check("bad table: plain status says it could not run", "could not run" in ts.out_of(g.rx(d, "status")))
    check("bad table: the good table still ran", (names.get("toy") or {}).get("ran") is True, names)

    print("[a failed build is a visible failure and cannot hide a linked line]")
    d = ts.toy_a("x-build")
    os.environ.update(g.ENV)  # the in-process engine must see the same per-user folder as the CLI runs
    eng = load_engine()
    cwd = os.getcwd()
    os.chdir(d)
    try:
        ctx = eng.Ctx(eng.find_root())
        st = ctx.state()
        old = eng._materialize
        eng._materialize = lambda *a, **k: None
        try:
            res = eng._sc_compute(ctx, st)
        finally:
            eng._materialize = old
        t = next((x for x in res["tables"] if x["name"] == "toy"), None)
        check("build: the table is reported failed", t is not None and not t.get("ran") and t.get("failed") is True, t)
        eng._sc_save(ctx, res)
        saved = {x["name"]: x for x in eng._sc_saved(ctx)["tables"]}
        check("build: the failure is saved and shown", bool((saved.get("toy") or {}).get("failed")), saved)
        status = eng._sc_case_status(ctx, {"table": "toy"})
        check("build: a linked line stays unproven", status != "passed" and status.startswith("shortcut table"), status)
    finally:
        os.chdir(cwd)

    print("[a table skipped because its watched files did not change keeps a saved result]")
    d = ts.toy_a("x-skip", other=True)
    (d / "other.py").write_text("X = 2\n")
    g.rx(d, "shortcuts")
    p = ts.evidence(d, "other")
    check("skip: the table ran and was saved", p.is_file() and json.loads(p.read_text()).get("ran") is True)
    g.git(d, "checkout", "--", "other.py")
    g.rx(d, "shortcuts")
    check("skip: the earlier saved result is still there", p.is_file() and json.loads(p.read_text()).get("ran") is True)



def main_edits_table_project(name):
    """Like m_project kind M2, but main also deletes the `tee` row, flips the `cat` row and swaps the runner for one
    that always answers ALLOWED, in the same commit as its guard change; then `merged` runs."""
    d = df._approved(name, steps=ts.MSTEPS, dm=df.DM3, branch="lane", extra_files=ts.toy_files())
    start = g.git(d, "rev-parse", "HEAD").stdout.strip()
    code1 = [c for c in ts.MSTEPS[0] if c.get("layer") == "code"]
    r = df._build(d, 1, code1, report=ts.MPASS1)
    assert r.returncode == 0, ts.out_of(r)
    g.git(d, "checkout", "-q", "-b", "main", start)
    (d / "guard.py").write_text(ts.GUARD_MAIN)
    edit_table(d, drop_and_flip)
    (d / "tests/shortcuts/toy_runner.py").write_text("print('ALLOWED')\n")
    ts._commit(d, "main change", "guard.py", "tests/shortcuts/toy.json", "tests/shortcuts/toy_runner.py")
    g.git(d, "checkout", "-q", "lane")
    r = g.git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    assert r.returncode == 0, r.stderr + r.stdout
    r = g.rx(d, "merged")
    assert r.returncode == 0, ts.out_of(r)
    (d / "guard.py").write_text(ts.GUARD_MAIN + "# lane edit after the merge\n")  # the guard changed in this run
    return d


def walk2():
    B = ts.BASE
    print("[main edits or removes a starting row (and the runner) after merged]")
    d = main_edits_table_project("x-main-table")
    r, j = ts.scj(d)
    check("main table: exits 0", ts.clean(r), ts.out_of(r)[-300:])
    rows_ok("main table", j, B, ts.GUARD_MAIN, new_guard(d))

    print("[a table and its runner made during the run read new and prove their line]")
    files = ts.toy_files(extra={"tests/shortcuts/toy.json": ts.table_json(runner="python3 tests/shortcuts/late_runner.py")})
    d = df._approved("x-fresh", steps=df.FIN2_STEPS, dm=df.DM2, extra_files=files)
    (d / "guard.py").write_text(ts.GUARD_GOOD)
    (d / "tests/shortcuts/late_runner.py").write_text(ts.RUNNER)
    (d / "tests/shortcuts/fresh_runner.py").write_text(ts.RUNNER)
    (d / "tests/shortcuts/fresh.json").write_text(
        ts.table_json(B, name="fresh", runner="python3 tests/shortcuts/fresh_runner.py"))
    r, j = ts.scj(d)
    check("fresh: exits 0", ts.clean(r), ts.out_of(r)[-300:])
    fr = ts.rows_of(j, "fresh")
    check("fresh: every row is new and judged on the current code",
          len(fr) == len(B) and all(x.get("new_row") is True and x.get("verdict") == "held" and x.get("old") == "none"
                                    for x in fr.values()), fr)
    os.environ.update(g.ENV)
    eng = load_engine()
    cwd = os.getcwd()
    os.chdir(d)
    try:
        ctx = eng.Ctx(eng.find_root())
        status = eng._sc_case_status(ctx, {"table": "fresh"})
    finally:
        os.chdir(cwd)
    check("fresh: a line linked to it is proven", status == "passed", status)
    tr = ts.rows_of(j, "toy")
    check("fresh: a starting table whose old runner is missing still reads errored",
          len(tr) == len(B) and all(x.get("verdict") == "errored" for x in tr.values()), tr)


def walk3():
    """[CP6-X01/X02 round 3] The runner and watch shapes are an allowlist: anything else is a visible table error."""
    B = ts.BASE
    bad_runners = {
        "interp": "bash tests/shortcuts/toy_runner.py",
        "args": "python3 tests/shortcuts/toy_runner.py extra",
        "bashc": "bash -c 'python3 tests/shortcuts/toy_runner.py'",
        "dotdot": "python3 tests/shortcuts/../toy_runner.py",
        "abs": "python3 /tmp/toy_runner.py",
        "cd": "cd tests && python3 shortcuts/toy_runner.py",
        "glob": "python3 tests/shortcuts/*.py",
        "outside": "python3 tests/shortcut_runner.py",
    }
    bad_watches = {
        "dotslash": "./guard.py",
        "dotdotw": "../guard.py",
        "absw": "/etc/passwd",
        "globw": "g*.py",
        "backslash": "tests\\guard.py",
        "nofile": "nosuchfile.py",
    }
    extra = {}
    for k, cmd in bad_runners.items():
        extra[f"tests/shortcuts/r{k}.json"] = ts.table_json(B[:1], name=f"r{k}", runner=cmd)
    for k, w in bad_watches.items():
        extra[f"tests/shortcuts/w{k}.json"] = ts.table_json(B[:1], name=f"w{k}", watches=(w,))
    print("[refused runner and watch shapes are visible table errors]")
    d = ts.toy_a("x-allow", extra=extra)
    r, j = ts.scj(d)
    for k, cmd in bad_runners.items():
        t = ts.tbl(j, f"r{k}")
        note = (t or {}).get("note") or ""
        check(f"runner {cmd!r}: shown as a table that could not run, naming the table and the runner",
              t is not None and t.get("ran") is False and t.get("failed") is True and f"r{k}" in note
              and "runner" in note and not t.get("rows"), t)
    for k, w in bad_watches.items():
        t = ts.tbl(j, f"w{k}")
        note = (t or {}).get("note") or ""
        check(f"watch {w!r}: shown as a table that could not run, naming the table",
              t is not None and t.get("ran") is False and t.get("failed") is True and f"w{k}" in note
              and not t.get("rows"), t)
    check("a refused table does not stop the good table", ts.rows_of(j, "toy") != {}, ts.tables_of(j))

    print("[a refused table linked to a line keeps that line unproven]")
    os.environ.update(g.ENV)
    eng = load_engine()
    cwd = os.getcwd()
    os.chdir(d)
    try:
        ctx = eng.Ctx(eng.find_root())
        status = eng._sc_case_status(ctx, {"table": "wdotslash"})
    finally:
        os.chdir(cwd)
    check("./guard.py table: a linked case is not passed", status != "passed", status)

    print("[plan-submit refuses a shortcuts case naming such a table]")
    for tag, files in (("runner", {"tests/shortcuts/bad.json": ts.table_json(B[:1], name="bad", runner=bad_runners["bashc"])}),
                       ("watch", {"tests/shortcuts/bad.json": ts.table_json(B[:1], name="bad", watches=("./guard.py",))})):
        d2, run = df._init_project(f"x-sub-{tag}", extra_files=ts.toy_files(extra=files))
        df._write_plan(run, [[ts.code_case("CP1-T01", [1]), ts.sc_case("CP1-T02", "bad", [2])]], df.DM2)
        r2 = g.rx(d2, "plan-submit")
        out = ts.out_of(r2)
        check(f"plan-submit with a bad {tag}: refused naming CP1-T02 and the table",
              r2.returncode != 0 and "CP1-T02" in out and "bad" in out and "Traceback" not in out, out[-500:])

    print("[the one accepted runner shape]")
    ok_p = getattr(eng, "_sc_runner_argv", None)
    for cmd in ("python3 tests/shortcuts/toy_runner.py",):
        argv = ok_p(cmd) if ok_p else (None, "missing")
        check(f"runner {cmd!r} is accepted as an argument list", argv[1] is None and argv[0], argv)
    bare = ok_p("tests/shortcuts/toy_runner.py") if ok_p else (None, "missing")
    check("a bare-file runner is refused, saying to write python3 <path>",
          bare[0] is None and "python3 tests/shortcuts/toy_runner.py" in (bare[1] or ""), bare)
    check("a canonical watch (file and folder) is accepted",
          all(eng._sc_parse(ts.table_json(B[:1], watches=(w,)))[1] is None for w in ("guard.py", "src/", "src/a.js")),
          None)
    print("[a watch added during the run for a file the run created]")
    d3 = ts.toy_a("x-newwatch", extra={"tests/shortcuts/nw.json": ts.table_json(B[:1], name="nw", watches=("made.py",),
                                                                            runner="python3 tests/shortcuts/toy_runner.py")})
    (d3 / "made.py").write_text("X = 1\n")
    r3, j3 = ts.scj(d3)
    t = ts.tbl(j3, "nw")
    check("nw: a watch of a file the starting tree lacks but the current tree has is not a table error",
          t is not None and not (t.get("failed") is True and "watches" in (t.get("note") or "")), t)


def walk4():
    """[CP6-X01 round 4] A row's expect is exactly "allowed" or "refused"; anything else is a visible table error."""
    B = ts.BASE
    bad = {"array": ["refused"], "object": {"a": 1}, "number": 5, "null": None, "unknown": "denied"}
    extra = {}
    for k, v in bad.items():
        rows = [dict(B[0], expect=v)]
        extra[f"tests/shortcuts/e{k}.json"] = ts.table_json(rows, name=f"e{k}")
    print("[bad expect values are visible table errors, never a crash]")
    d = ts.toy_a("x-expect", extra=extra)
    r, j = ts.scj(d)
    check("shortcuts did not crash", r.returncode == 0 and "Traceback" not in ts.out_of(r), ts.out_of(r)[-400:])
    for k in bad:
        t = ts.tbl(j, f"e{k}")
        note = (t or {}).get("note") or ""
        check(f"expect {k}: shown as a table that could not run, naming the table and expect",
              t is not None and t.get("ran") is False and t.get("failed") is True and f"e{k}" in note
              and "expect" in note and not t.get("rows"), t)
    check("a good table still runs", ts.rows_of(j, "toy") != {}, ts.tables_of(j))
    print("[plan-submit refuses a shortcuts case naming a table with a bad expect]")
    files = {"tests/shortcuts/bad.json": ts.table_json([dict(B[0], expect=["refused"])], name="bad")}
    d2, run = df._init_project("x-sub-expect", extra_files=ts.toy_files(extra=files))
    df._write_plan(run, [[ts.code_case("CP1-T01", [1]), ts.sc_case("CP1-T02", "bad", [2])]], df.DM2)
    r2 = g.rx(d2, "plan-submit")
    out = ts.out_of(r2)
    check("plan-submit: refused naming CP1-T02 and the table, no traceback",
          r2.returncode != 0 and "CP1-T02" in out and "bad" in out and "Traceback" not in out, out[-500:])


def main():
    try:
        walk()
        walk2()
        walk3()
        walk4()
    except Exception as e:
        traceback.print_exc()
        FAILS.append(f"crashed: {e}")
    print(f"\n{'FAILED' if FAILS else 'ok'}: {len(FAILS)} failing check(s)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
