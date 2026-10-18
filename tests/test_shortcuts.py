#!/usr/bin/env python3
"""
CP6 tests: the before-and-after shortcut table.   python3 tests/test_shortcuts.py

For a guard-like change Ratchet runs a fixed table of shortcut commands (tests/shortcuts/<name>.json:
{name, watches, runner, rows[{id, command, expect}]}) against the code the run started from and the current code
and shows which rows got worse, stayed open, got fixed or errored. `ratchet.py shortcuts [<name>] [--json]` runs
the tables whose watched files changed in this run; the last step's complete runs it too and saves the result in
evidence/shortcuts/<name>.json; the final review, status --json (key 'shortcuts') and the receipt show it; a test
case of layer 'shortcuts' (field "table") links a table to done-means lines.

Every case drives the real CLI in a throwaway git project that holds a toy guard (guard.py) and a toy table
(tests/shortcuts/toy.json + toy_runner.py) committed BEFORE the run starts, so the run's starting commit holds
the old guard; the working tree then gets the new guard. Project helpers come from tests/test_dm_finish.py
(_approved, _build, _init_project, _write_plan), tests/test_dm_table.py (mutate, _table), tests/test_gates.py
and tests/test_receipt_proof.py. Every assertion goes through check(), so the missing feature fails cleanly.

Assumed wording (the build must print it): the plain output has one heading line per verdict that starts with
"got worse", "still open", "fixed", "errored" (no comma on the line), the rows under it show their command, and
the final review, status and receipt blocks contain "shortcut table".

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                      # noqa: E402
import test_gates as g           # noqa: E402
import test_receipt_proof as rp  # noqa: E402
import test_dm_table as dt       # noqa: E402
import test_dm_finish as df      # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP6-[TX]\d+[a-z]?)\]")


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


# ------------------------------------------------------------------------------------------------ toy guard

GUARD_START = "RULES = [\n\n    lambda c: '>' in c,\n\n    lambda c: 'tee' in c,\n\n]\n"
GUARD_NEW = ("RULES = [\n\n    lambda c: '>' in c,\n\n    lambda c: 'cp -t' in c,\n\n"
             "    lambda c: c.startswith('diff'),\n\n]\n")
GUARD_GOOD = ("RULES = [\n\n    lambda c: '>' in c,\n\n    lambda c: 'tee' in c,\n\n    lambda c: 'cp -t' in c,\n\n"
              "    lambda c: 'sed -i' in c,\n\n]\n")
GUARD_NEW_V2 = ("RULES = [\n\n    lambda c: '>' in c,\n\n    lambda c: 'tee' in c,\n\n    lambda c: 'cp -t' in c,\n\n]\n")
GUARD_LANE = "RULES = [\n\n    lambda c: '>' in c,\n\n]\n"                    # the lane removed the tee rule
GUARD_MAIN = ("RULES = [\n\n    lambda c: 'sed -i' in c,\n\n    lambda c: '>' in c,\n\n"
              "    lambda c: 'tee' in c,\n\n]\n")                                  # main added a sed -i rule
GUARD_MAIN_DD = GUARD_MAIN.replace("RULES = [\n\n", "RULES = [\n\n    lambda c: c.startswith('dd '),\n\n")   # main's guard that also refuses dd
assert GUARD_MAIN_DD != GUARD_MAIN, "GUARD_MAIN_DD replace anchor no longer matches GUARD_MAIN"
GUARD_LANE3 = "RULES = [\n\n    lambda c: '>' in c,\n\n    lambda c: 'tee ' in c,\n\n]\n"
GUARD_MAIN3 = "RULES = [\n\n    lambda c: '>' in c,\n\n    lambda c: 'tee -a' in c,\n\n]\n"

RUNNER = '''import os, sys
sys.dont_write_bytecode = True
root = os.environ["RATCHET_SHORTCUT_ROOT"]
cmd = os.environ["RATCHET_SHORTCUT_COMMAND"]
if cmd == "CRASH":
    raise RuntimeError("boom from the toy runner")
if cmd == "PRINT_THEN_CRASH":
    print("REFUSED")
    sys.stderr.write("late failure\\n")
    sys.exit(1)
if cmd == "GARBAGE":
    print("refused: probably")
    sys.exit(0)
if cmd == "EMPTY":
    sys.exit(0)
if cmd == "TWO_LINES":
    print("ALLOWED")
    print("REFUSED")
    sys.exit(0)
if cmd == "WRITE":
    with open(os.path.join(root, "written_by_runner.txt"), "w") as f:
        f.write("x")
ns = {}
with open(os.path.join(root, "guard.py")) as f:
    exec(compile(f.read(), "guard.py", "exec"), ns)
print("REFUSED" if any(r(cmd) for r in ns["RULES"]) else "ALLOWED")
'''

C_RED, C_TEE, C_CP, C_SED = "echo x > state.json", "cat x | tee state.json", "cp -t dir f", "sed -i s/a/b/ f"
C_CAT, C_DIFF = "cat state.json", "diff -u a b"
BASE = [{"id": "redirect", "command": C_RED, "expect": "refused"},
        {"id": "tee", "command": C_TEE, "expect": "refused"},
        {"id": "cp_t", "command": C_CP, "expect": "refused"},
        {"id": "sed_i", "command": C_SED, "expect": "refused"},
        {"id": "cat", "command": C_CAT, "expect": "allowed"},
        {"id": "diff", "command": C_DIFF, "expect": "allowed"}]
CRASH_ROW = {"id": "crash", "command": "CRASH", "expect": "refused"}
LATE_ROW = {"id": "late_crash", "command": "PRINT_THEN_CRASH", "expect": "refused"}
RUNNER_CMD = "python3 tests/shortcuts/toy_runner.py"
SC_RE = re.compile(r"shortcut[ -]table", re.I)


def table_json(rows=None, name="toy", watches=("guard.py",), runner=RUNNER_CMD):
    return json.dumps({"name": name, "watches": list(watches), "runner": runner,
                       "rows": [dict(r) for r in (rows if rows is not None else BASE)]}, indent=1) + "\n"


def toy_files(rows=None, other=False, extra=None):
    files = {"guard.py": GUARD_START, "tests/shortcuts/toy_runner.py": RUNNER,
             "tests/shortcuts/toy.json": table_json(rows)}
    if other:
        files["other.py"] = "X = 1\n"
        files["tests/shortcuts/other.json"] = table_json([BASE[0]], name="other", watches=("other.py",))
    files.update(extra or {})
    return files


def toy_a(name, rows=None, steps=None, dm=None, new=GUARD_NEW, other=False, extra=None, human_review="end"):
    """Toy project A: committed old guard + table, one approved plan, then the new guard in the working tree."""
    d = df._approved(name, steps=steps or df.FIN2_STEPS, dm=dm or df.DM2, human_review=human_review,
                     extra_files=toy_files(rows, other, extra))
    if new is not None:
        (d / "guard.py").write_text(new)
    return d


def sc_case(cid, table, proves):
    c = {"id": cid, "title": f"Shortcut table {table or '(none)'} holds", "layer": "shortcuts", "proves": list(proves)}
    if table is not None:
        c["table"] = table
    return c


def code_case(cid, proves):
    return df._case(cid, proves)


# ------------------------------------------------------------------------------------------------ readers

def run_dir(d):
    return Path(d) / ".ratchet/runs/f"


def out_of(r):
    return (r.stdout or "") + (r.stderr or "")


def scj(d, *extra):
    r = g.rx(d, "shortcuts", *extra, "--json")
    try:
        j = json.loads(r.stdout)
    except Exception:
        j = None
    return r, j


def tables_of(v):
    if isinstance(v, dict) and isinstance(v.get("tables"), list):
        return v["tables"]
    if isinstance(v, list):
        return v
    if isinstance(v, dict) and isinstance(v.get("rows"), list):
        return [v]
    return []


def tbl(j, name="toy"):
    for t in tables_of(j):
        if isinstance(t, dict) and t.get("name") == name:
            return t
    return None


def rows_of(j, name="toy"):
    t = tbl(j, name)
    return {r.get("id"): r for r in ((t or {}).get("rows") or []) if isinstance(r, dict)}


def counts_of(j, name="toy"):
    return (tbl(j, name) or {}).get("counts") or {}


def verdicts(j, name="toy"):
    return {k: v.get("verdict") for k, v in rows_of(j, name).items()}


HEADS = {"worse": r"got worse", "still_open": r"still open", "fixed": r"fixed", "errored": r"errored",
         "unknown": r"unknown"}


def sections(out):
    """The plain output split by its verdict heading lines: {kind: [lines under it]}."""
    res = {k: [] for k in HEADS}
    cur = None
    for ln in out.splitlines():
        s = re.sub(r"^[\W_]*(?:rows\s+(?:that\s+)?)?", "", ln.strip(), flags=re.I)
        hit = None
        if "," not in ln:
            for k, p in HEADS.items():
                if re.match(p + r"\b", s, re.I):
                    hit = k
                    break
        if hit:
            cur = hit
        elif cur:
            res[cur].append(ln)
    return res


def kinds_of(out, cmd):
    secs = sections(out)
    return {k for k, lines in secs.items() if any(cmd in ln for ln in lines)}


def count_re(n, word):
    return re.compile(rf"\b{n}\s+{word}\b|\b{word}\b\s*[:=]?\s*{n}\b", re.I)


def has_counts(text, worse, still, fixed, held, errored):
    return all(count_re(n, w).search(text) for n, w in
               ((worse, "worse"), (still, "still open"), (fixed, "fixed"), (held, "held"), (errored, "errored")))


def clean(r):
    return r.returncode == 0 and "Traceback" not in out_of(r)


def sjson(d):
    r = g.rx(d, "status", "--json")
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def evidence(d, name="toy"):
    return run_dir(d) / "evidence/shortcuts" / f"{name}.json"


def state(d):
    return json.loads((run_dir(d) / "state.json").read_text())


def row_is(tag, rows, rid, old, new, verdict):
    r = rows.get(rid) or {}
    check(f"{tag} row {rid}: old {old}, new {new}, verdict {verdict}",
          r.get("old") == old and r.get("new") == new and r.get("verdict") == verdict, r)


BASE_VERDICTS = {"redirect": "held", "tee": "worse", "cp_t": "fixed", "sed_i": "still_open", "cat": "held",
                 "diff": "worse"}

# ------------------------------------------------------------------------------------------------ finished runs

_FIN: dict = {}


def finished(kind):
    """Toy project A built through its only step (guard edited before the behavior gate); kind 'plain' or 'crash'."""
    if kind not in _FIN:
        rows = BASE + ([CRASH_ROW] if kind == "crash" else [])
        d = toy_a(f"fin-{kind}", rows=rows)
        r = df._build(d, 1, df.FIN2_STEPS[0], report=df.PASSED2)
        _FIN[kind] = (d, r)
    return _FIN[kind]


# ------------------------------------------------------------------------------------------------ merge projects

MAIN_ROW = {"id": "dd", "command": "dd if=x of=state.json", "expect": "refused"}
MSTEPS = [[code_case("CP1-T01", [1])], [code_case("CP2-T01", [2]), code_case("CP2-T02", [3])]]
MPASS1 = [{"id": "CP1-T01", "status": "passed"}]
MPASS2 = [{"id": "CP2-T01", "status": "passed"}, {"id": "CP2-T02", "status": "passed"}]


def _commit(d, msg, *paths):
    """Commit only the named paths: a branch switch must never sweep the run's own files into a commit."""
    g.git(d, "add", *paths)
    r = g.git(d, "commit", "-qm", msg)
    assert r.returncode == 0, r.stderr + r.stdout


def m_project(name, kind="M", rows=None, steps=None, dm=None, strip_run_start=False, merge=True, main_guard=None):
    """Toy project M (kind 'M'), M2 (the lane changes an unrelated file, main changes the guard) or M3 (lane and
    main edit the same guard line, the conflict is resolved by hand). A two-step lane: step 1 completes with the
    lane's change, main is forked from the run's STARTING commit and gets its own commit, main is merged with git
    and (when merge) `ratchet.py merged` runs. Returns (project dir, run's starting commit)."""
    steps = steps or MSTEPS
    d = df._approved(name, steps=steps, dm=dm or df.DM3, branch="lane", extra_files=toy_files(rows))
    start = g.git(d, "rev-parse", "HEAD").stdout.strip()
    lane_guard = {"M": GUARD_LANE, "M2": None, "M3": GUARD_LANE3}[kind]
    if lane_guard:
        (d / "guard.py").write_text(lane_guard)
    code1 = [c for c in steps[0] if c.get("layer") == "code"]
    r = df._build(d, 1, code1, report=[{"id": c["id"], "status": "passed"} for c in code1])
    assert r.returncode == 0, out_of(r)
    r = g.git(d, "checkout", "-q", "-b", "main", start)
    assert r.returncode == 0, r.stderr
    (d / "guard.py").write_text(main_guard or {"M": GUARD_MAIN, "M2": GUARD_MAIN, "M3": GUARD_MAIN3}[kind])
    if kind == "M":
        t = json.loads((d / "tests/shortcuts/toy.json").read_text())
        t["rows"].append(dict(MAIN_ROW))
        (d / "tests/shortcuts/toy.json").write_text(json.dumps(t, indent=1) + "\n")
    _commit(d, "main change", "guard.py", "tests/shortcuts/toy.json")
    r = g.git(d, "checkout", "-q", "lane")
    assert r.returncode == 0, r.stderr
    if strip_run_start:
        dt.mutate(d, lambda st: st.pop("run_start", None))
    r = g.git(d, "merge", "--no-ff", "-q", "-m", "Merge main into lane", "main")
    if kind == "M3":
        assert r.returncode != 0, "expected a merge conflict"
        (d / "guard.py").write_text(GUARD_LANE3)
        g.git(d, "add", "-A")
        r = g.git(d, "commit", "-qm", "Merge main into lane (resolved by hand)")
    assert r.returncode == 0, r.stderr + r.stdout
    if merge:
        r = g.rx(d, "merged")
        assert r.returncode == 0, out_of(r)
    return d, start


# ------------------------------------------------------------------------------------------------ cases

def cp6_t01_worse_rows():
    """[CP6-T01] A shortcut that used to be blocked and now gets through is shown as worse."""
    d = toy_a("t01")
    r, j = scj(d)
    check("[CP6-T01] shortcuts --json exits 0 with JSON", clean(r) and j is not None, out_of(r)[-500:])
    rows = rows_of(j)
    row_is("[CP6-T01]", rows, "tee", "REFUSED", "ALLOWED", "worse")
    row_is("[CP6-T01]", rows, "diff", "ALLOWED", "REFUSED", "worse")
    p = g.rx(d, "shortcuts")
    out = out_of(p)
    check("[CP6-T01] plain shortcuts exits 0", clean(p), out[-500:])
    check("[CP6-T01] tee and diff are named under 'got worse' and nowhere else",
          kinds_of(out, C_TEE) == {"worse"} and kinds_of(out, C_DIFF) == {"worse"},
          (kinds_of(out, C_TEE), kinds_of(out, C_DIFF), out[-700:]))


def cp6_t02_still_open():
    """[CP6-T02] A hole that was open at the start and is still open is shown as still open."""
    d = toy_a("t02")
    r, j = scj(d)
    row_is("[CP6-T02]", rows_of(j), "sed_i", "ALLOWED", "ALLOWED", "still_open")
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T02] sed -i is listed under 'still open' only", kinds_of(out, C_SED) == {"still_open"},
          (kinds_of(out, C_SED), out[-700:]))


def cp6_t03_fixed():
    """[CP6-T03] A hole the run closed is shown as fixed."""
    d = toy_a("t03")
    r, j = scj(d)
    row_is("[CP6-T03]", rows_of(j), "cp_t", "ALLOWED", "REFUSED", "fixed")
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T03] cp -t is listed under 'fixed' only", kinds_of(out, C_CP) == {"fixed"},
          (kinds_of(out, C_CP), out[-700:]))


def cp6_t04_held_and_counts():
    """[CP6-T04] Rows that are right in both versions are not flagged, and the counts add up."""
    d = toy_a("t04")
    r, j = scj(d)
    v = verdicts(j)
    check("[CP6-T04] redirect and cat are held", v.get("redirect") == "held" and v.get("cat") == "held", v)
    c = counts_of(j)
    want = {"worse": 2, "still_open": 1, "fixed": 1, "held": 2, "errored": 0, "unknown": 0}
    check("[CP6-T04] counts are worse 2, still_open 1, fixed 1, held 2, errored 0, unknown 0",
          all(c.get(k) == n for k, n in want.items()), c)
    check("[CP6-T04] counts sum to the six rows", sum(c.get(k, 0) for k in want) == 6, c)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T04] redirect and cat are not named under any flagged heading",
          kinds_of(out, C_RED) == set() and kinds_of(out, C_CAT) == set(), (kinds_of(out, C_RED), kinds_of(out, C_CAT)))
    check("[CP6-T04] the plain output states the counts in one line", has_counts(out, 2, 1, 1, 2, 0), out[-700:])


def cp6_t05_crash_is_error():
    """[CP6-T05] A runner that crashes shows as an error, never as refused."""
    d = toy_a("t05", rows=BASE + [CRASH_ROW, LATE_ROW])
    r, j = scj(d)
    check("[CP6-T05] exits 0 with JSON", clean(r) and j is not None, out_of(r)[-500:])
    rows = rows_of(j)
    for rid in ("crash", "late_crash"):
        x = rows.get(rid) or {}
        check(f"[CP6-T05] row {rid} is old error, new error, errored",
              x.get("old") == "error" and x.get("new") == "error" and x.get("verdict") == "errored", x)
    c = counts_of(j)
    check("[CP6-T05] counts.errored is 2 and the other counts are those of the six base rows",
          c.get("errored") == 2 and c.get("worse") == 2 and c.get("held") == 2 and c.get("fixed") == 1
          and c.get("still_open") == 1, c)
    check("[CP6-T05] the six base rows keep their normal verdicts",
          all(verdicts(j).get(k) == v for k, v in BASE_VERDICTS.items()), verdicts(j))
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T05] both rows are under 'errored' only",
          kinds_of(out, "CRASH") == {"errored"} and kinds_of(out, "PRINT_THEN_CRASH") == {"errored"},
          (kinds_of(out, "CRASH"), kinds_of(out, "PRINT_THEN_CRASH")))
    check("[CP6-T05] the crash row shows the runner's last line of output", "boom from the toy runner" in out, out[-700:])


def cp6_t06_garbage_is_error():
    """[CP6-T06] Anything the runner prints other than exactly ALLOWED or REFUSED shows as an error."""
    rows = BASE + [{"id": "garbage", "command": "GARBAGE", "expect": "refused"},
                   {"id": "empty", "command": "EMPTY", "expect": "refused"},
                   {"id": "two", "command": "TWO_LINES", "expect": "refused"}]
    broken = table_json([{"id": "x", "command": C_RED, "expect": "refused"}], name="brokenrunner",
                        runner="python3 tests/shortcuts/missing_runner.py")
    d = toy_a("t06", rows=rows, extra={"tests/shortcuts/brokenrunner.json": broken})
    r, j = scj(d)
    check("[CP6-T06] exits 0 with JSON", clean(r) and j is not None, out_of(r)[-500:])
    v = verdicts(j)
    check("[CP6-T06] GARBAGE, EMPTY and TWO_LINES rows are errored",
          all(v.get(k) == "errored" for k in ("garbage", "empty", "two")), v)
    check("[CP6-T06] the base rows still get their normal verdicts",
          all(v.get(k) == x for k, x in BASE_VERDICTS.items()), v)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T06] the output says it was not ALLOWED or REFUSED", re.search(r"ALLOWED or REFUSED", out) is not None,
          out[-700:])
    bv = verdicts(j, "brokenrunner")
    check("[CP6-T06] a runner that cannot be started gives errored for its rows and does not abort the others",
          bv.get("x") == "errored", (bv, tables_of(j)))


def cp6_t07_deleted_row_still_runs():
    """[CP6-T07] A row deleted from the table file after the run started still runs."""
    d = toy_a("t07")
    t = json.loads((d / "tests/shortcuts/toy.json").read_text())
    t["rows"] = [x for x in t["rows"] if x["id"] != "tee"]
    (d / "tests/shortcuts/toy.json").write_text(json.dumps(t))
    r, j = scj(d)
    x = rows_of(j).get("tee") or {}
    check("[CP6-T07] tee is still there with its command, expectation refused and verdict worse",
          x.get("command") == C_TEE and x.get("expect") == "refused" and x.get("verdict") == "worse", x)
    c = counts_of(j)
    check("[CP6-T07] counts are those of CP6-T04", c.get("worse") == 2 and c.get("held") == 2 and c.get("fixed") == 1
          and c.get("still_open") == 1, c)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T07] the output says the table file no longer has this row",
          re.search(r"no longer|removed|deleted", out, re.I) is not None and "tee" in out, out[-700:])


def cp6_t08_flipped_row_keeps_expectation():
    """[CP6-T08] A row flipped to expect the opposite after the run started keeps its original expectation."""
    d = toy_a("t08")
    t = json.loads((d / "tests/shortcuts/toy.json").read_text())
    for x in t["rows"]:
        if x["id"] == "sed_i":
            x["expect"] = "allowed"
        if x["id"] == "tee":
            x["expect"] = "allowed"
            x["command"] = "true"
    (d / "tests/shortcuts/toy.json").write_text(json.dumps(t))
    r, j = scj(d)
    rows = rows_of(j)
    s = rows.get("sed_i") or {}
    check("[CP6-T08] sed_i is still still_open with expect refused",
          s.get("verdict") == "still_open" and s.get("expect") == "refused", s)
    x = rows.get("tee") or {}
    check("[CP6-T08] tee is still worse with expect refused and its original command",
          x.get("verdict") == "worse" and x.get("expect") == "refused" and x.get("command") == C_TEE, x)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T08] the output notes the rows changed since the run started and the starting version was used",
          re.search(r"changed", out, re.I) is not None and re.search(r"start", out, re.I) is not None
          and "sed_i" in out and "tee" in out, out[-700:])


def cp6_t09_deleted_table_file():
    """[CP6-T09] Deleting the whole table file, or the whole shortcuts folder, does not make the table go away."""
    for variant in ("file", "folder"):
        d = toy_a(f"t09-{variant}")
        if variant == "file":
            (d / "tests/shortcuts/toy.json").unlink()
        else:
            shutil.rmtree(d / "tests/shortcuts")
        r, j = scj(d)
        check(f"[CP6-T09] ({variant}) exits 0", clean(r), out_of(r)[-500:])
        check(f"[CP6-T09] ({variant}) table toy still runs with the six verdicts of CP6-T04",
              verdicts(j) == BASE_VERDICTS, verdicts(j))
        out = out_of(g.rx(d, "shortcuts"))
        check(f"[CP6-T09] ({variant}) the output says the table file was deleted since the run started",
              re.search(r"deleted|no longer|removed|missing", out, re.I) is not None, out[-700:])


def cp6_t10_new_rows_and_tables():
    """[CP6-T10] A row or table added during the run also runs and is marked new."""
    d = toy_a("t10")
    t = json.loads((d / "tests/shortcuts/toy.json").read_text())
    t["rows"].append({"id": "newrow", "command": "echo y >> log", "expect": "refused"})
    (d / "tests/shortcuts/toy.json").write_text(json.dumps(t))
    (d / "tests/shortcuts/fresh.json").write_text(
        table_json([{"id": "f1", "command": C_RED, "expect": "refused"},
                    {"id": "f2", "command": C_CAT, "expect": "allowed"}], name="fresh"))
    r, j = scj(d)
    rows = rows_of(j)
    n = rows.get("newrow") or {}
    check("[CP6-T10] newrow is new_row true with old REFUSED, new REFUSED, held",
          n.get("new_row") is True and n.get("old") == "REFUSED" and n.get("new") == "REFUSED"
          and n.get("verdict") == "held", n)
    check("[CP6-T10] the six base rows have new_row false", all((rows.get(k) or {}).get("new_row") is False
                                                                 for k in BASE_VERDICTS), rows)
    fr = rows_of(j, "fresh")
    check("[CP6-T10] table fresh runs with every row new_row true",
          len(fr) == 2 and all(x.get("new_row") is True for x in fr.values()) and (tbl(j, "fresh") or {}).get("ran") is True,
          tbl(j, "fresh"))


def cp6_t11_only_when_watched_files_changed():
    """[CP6-T11] A table runs only when the files it watches changed in this run."""
    d = toy_a("t11", other=True)
    r, j = scj(d)
    check("[CP6-T11] toy ran with results", (tbl(j) or {}).get("ran") is True and rows_of(j).get("tee", {}).get("verdict")
          == "worse", tbl(j))
    o = tbl(j, "other")
    check("[CP6-T11] other is shown with ran false", o is not None and o.get("ran") is False, tables_of(j))
    check("[CP6-T11] no result file is saved for other", not evidence(d, "other").exists(),
          list((run_dir(d) / "evidence").rglob("*")) if (run_dir(d) / "evidence").exists() else None)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T11] the plain output says other was not run because its watched files did not change",
          re.search(r"other", out) is not None and re.search(r"did not change|not changed|unchanged", out, re.I) is not None,
          out[-700:])
    (d / "guard.py").write_text(GUARD_START)
    r2, j2 = scj(d)
    check("[CP6-T11] with guard.py back to its starting content toy is not run either",
          (tbl(j2) or {}).get("ran") is False, tables_of(j2))


def cp6_t12_complete_runs_the_table():
    """[CP6-T12] Completing the last step runs the table and saves its result without blocking the finish."""
    d, r = finished("plain")
    check("[CP6-T12] complete exits 0 though rows are worse", r is not None and clean(r), out_of(r)[-600:] if r else None)
    p = evidence(d)
    check("[CP6-T12] evidence/shortcuts/toy.json exists", p.exists(), p)
    text = p.read_text() if p.exists() else ""
    check("[CP6-T12] the saved file holds the verdicts", "worse" in text and "still_open" in text and C_TEE in text, text[:300])
    check("[CP6-T12] the phase is the final review", state(d).get("phase") == "awaiting_final_review", state(d).get("phase"))
    d2, r2 = finished("crash")
    check("[CP6-T12] a run whose table has a crashing row still completes", r2 is not None and clean(r2),
          out_of(r2)[-600:] if r2 else None)
    check("[CP6-T12] the crash run saved its result too", evidence(d2).exists(), evidence(d2))


def cp6_t13_final_review_shows_table():
    """[CP6-T13] The final review shows which rows got worse, stayed open, got fixed or errored, with counts."""
    d, _ = finished("plain")
    s = g.rx(d, "status")
    out = out_of(s)
    check("[CP6-T13] status exits 0 and has a shortcut-table block naming toy", clean(s) and SC_RE.search(out) is not None
          and "toy" in out, out[-1200:])
    check("[CP6-T13] the block shows the counts 2 worse, 1 still open, 1 fixed, 2 held, 0 errored",
          has_counts(out, 2, 1, 1, 2, 0), out[-1200:])
    check("[CP6-T13] the commands of the worse rows are shown", C_TEE in out and C_DIFF in out, out[-1200:])
    j = sjson(d)
    t = tbl(j.get("shortcuts"))
    check("[CP6-T13] status --json carries the table under 'shortcuts' with the same counts and rows",
          t is not None and (t.get("counts") or {}).get("worse") == 2 and (t.get("counts") or {}).get("held") == 2
          and (t.get("counts") or {}).get("fixed") == 1 and (t.get("counts") or {}).get("still_open") == 1
          and len(t.get("rows") or []) == 6, j.get("shortcuts"))
    nd, _ = df._fin2("t13-none", failed=False)
    ns = g.rx(nd, "status")
    nj = sjson(nd)
    check("[CP6-T13] a run with no table whose files changed shows no table block and no error",
          clean(ns) and SC_RE.search(out_of(ns)) is None
          and not [t for t in tables_of(nj.get("shortcuts")) if t.get("ran")], (out_of(ns)[-500:], nj.get("shortcuts")))


def cp6_t14_receipt_shows_table():
    """[CP6-T14] The receipt shows the same table result as the final review."""
    d, _ = finished("plain")
    r = g.rx(d, "receipt")
    out = out_of(r)
    check("[CP6-T14] the markdown receipt has a shortcut-table section with the counts and the worse rows",
          clean(r) and SC_RE.search(out) is not None and has_counts(out, 2, 1, 1, 2, 0) and C_TEE in out
          and C_DIFF in out, out[-1500:])
    rj = g.rx(d, "receipt", "--json")
    try:
        doc = json.loads(rj.stdout)
    except Exception:
        doc = {}
    keys = [k for k in doc if "shortcut" in k.lower()] if isinstance(doc, dict) else []
    t = tbl(doc[keys[0]]) if keys else None
    check("[CP6-T14] the JSON receipt carries the result under one 'shortcut' key",
          len(keys) == 1 and t is not None and (t.get("counts") or {}).get("worse") == 2
          and (t.get("counts") or {}).get("held") == 2, keys)
    d2, _ = finished("crash")
    out2 = out_of(g.rx(d2, "receipt"))
    check("[CP6-T14] an errored row is listed as errored, never as refused or held",
          kinds_of(out2, "CRASH") == {"errored"}, (kinds_of(out2, "CRASH"), out2[-900:]))


def cp6_t15_stale_result():
    """[CP6-T15] A saved table result is marked stale once the code changes again."""
    d = toy_a("t15")
    r, j = scj(d)
    check("[CP6-T15] the first run is clean and not stale", clean(r) and (tbl(j) or {}).get("stale") is False, tbl(j))
    (d / "guard.py").write_text(GUARD_NEW_V2)
    s = out_of(g.rx(d, "status"))
    sj = sjson(d)
    t = tbl(sj.get("shortcuts"))
    check("[CP6-T15] status says the saved result is out of date", re.search(r"out of date|stale", s, re.I) is not None, s[-800:])
    check("[CP6-T15] status --json has stale true", t is not None and t.get("stale") is True, sj.get("shortcuts"))
    r2, j2 = scj(d)
    check("[CP6-T15] after shortcuts runs again the stale mark is gone", (tbl(j2) or {}).get("stale") is False, tbl(j2))
    check("[CP6-T15] the verdicts reflect the new guard: tee is held now", verdicts(j2).get("tee") == "held", verdicts(j2))
    t2 = tbl(sjson(d).get("shortcuts"))
    check("[CP6-T15] status --json no longer says stale", t2 is not None and t2.get("stale") is False, t2)


def _hashes(d):
    d = Path(d)
    out = {}
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if rel.parts[0] == ".git" or rel.as_posix().startswith(".ratchet/runs"):
            continue
        if p.is_file():
            out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _tmp_occ():
    bases = {Path(tempfile.gettempdir()), Path(g.ENV.get("TMPDIR") or tempfile.gettempdir())}
    return {str(p) for b in bases for p in b.glob("ratchet-occurrence-*")}


def _git_view(d):
    return (g.git(d, "status", "--porcelain").stdout, g.git(d, "for-each-ref").stdout, g.git(d, "stash", "list").stdout)


def cp6_t16_changes_nothing():
    """[CP6-T16] Running the table changes no file in the project and leaves no copies behind."""
    wrow = {"id": "write", "command": "WRITE", "expect": "allowed"}
    d = toy_a("t16a", rows=BASE + [wrow])
    r = df._build(d, 1, df.FIN2_STEPS[0], report=df.PASSED2, complete=False)
    (d / "notes.txt").write_text("untracked\n")
    (d / "src/a.js").write_text("export const a = 2;\n")
    occ, hs, gv = _tmp_occ(), _hashes(d), _git_view(d)
    ra, rb = g.rx(d, "shortcuts"), g.rx(d, "shortcuts", "--json")
    check("[CP6-T16] shortcuts and shortcuts --json exit 0", clean(ra) and clean(rb), (out_of(ra)[-300:], out_of(rb)[-300:]))
    check("[CP6-T16] A: every file is unchanged", _hashes(d) == hs, set(_hashes(d).items()) ^ set(hs.items()))
    check("[CP6-T16] A: git status, refs and stash are unchanged", _git_view(d) == gv, (_git_view(d), gv))
    check("[CP6-T16] A: working guard.py still holds the NEW rules", (d / "guard.py").read_text() == GUARD_NEW, None)
    check("[CP6-T16] A: no ratchet-occurrence folder is left", _tmp_occ() == occ, _tmp_occ() - occ)
    check("[CP6-T16] A: a runner that writes into its version root left nothing in the project",
          not list(Path(d).rglob("written_by_runner.txt")), list(Path(d).rglob("written_by_runner.txt")))
    (d / "notes.txt").unlink()
    (d / "src/a.js").write_text("export const a = 1;\n")
    rc = g.rx(d, "complete", "CP1")
    check("[CP6-T16] A: complete (with its table call) exits 0", clean(rc), out_of(rc)[-500:])
    check("[CP6-T16] A: after complete no occurrence folder is left and no runner file is in the project",
          _tmp_occ() == occ and not list(Path(d).rglob("written_by_runner.txt")), _tmp_occ() - occ)
    check("[CP6-T16] A: the guard still holds the NEW rules after complete",
          (d / "guard.py").read_text() == GUARD_NEW, (d / "guard.py").read_text())
    m, _ = m_project("t16m", "M", rows=BASE + [wrow])
    (m / "notes.txt").write_text("untracked\n")
    (m / "src/a.js").write_text("export const a = 3;\n")
    occ, hs, gv = _tmp_occ(), _hashes(m), _git_view(m)
    ra, rb = g.rx(m, "shortcuts"), g.rx(m, "shortcuts", "--json")
    check("[CP6-T16] M: shortcuts and shortcuts --json exit 0", clean(ra) and clean(rb), (out_of(ra)[-300:], out_of(rb)[-300:]))
    check("[CP6-T16] M: every file is unchanged", _hashes(m) == hs, set(_hashes(m).items()) ^ set(hs.items()))
    check("[CP6-T16] M: git status, refs (no new branch) and stash are unchanged", _git_view(m) == gv, (_git_view(m), gv))
    check("[CP6-T16] M: no occurrence folder is left and the runner wrote nothing in the project",
          _tmp_occ() == occ and not list(Path(m).rglob("written_by_runner.txt")), _tmp_occ() - occ)


def _line_seg(out, dm, i):
    return rp._dm_segment(out, dm, i)


def cp6_t17_linked_line_unproven():
    """[CP6-T17] A done-means line linked to a table is unproven while any row is wrong on the current code."""
    steps = [[sc_case("CP1-T01", "toy", [1]), code_case("CP1-T02", [2])]]
    d = toy_a("t17", rows=BASE + [CRASH_ROW], steps=steps)
    before = dt._table(d)
    out0 = out_of(g.rx(d, "receipt"))
    check("[CP6-T17] before `shortcuts` ran, the shortcuts case reads exactly 'no result recorded yet'",
          isinstance(before, list) and len(before) == 2 and dt._case_result(before[0], "CP1-T01") == "no result recorded yet"
          and "no result recorded yet" in _line_seg(out0, df.DM2, 0), before)
    r = g.rx(d, "shortcuts")
    check("[CP6-T17] shortcuts exits 0", clean(r), out_of(r)[-500:])
    after = dt._table(d)
    out = out_of(g.rx(d, "receipt"))
    seg = _line_seg(out, df.DM2, 0)
    check("[CP6-T17] line 1 is unproven in the done-means table",
          isinstance(after, list) and len(after) == 2 and after[0].get("verdict") == "unproven", after)
    check("[CP6-T17] the shortcuts case no longer reads 'no result recorded yet'",
          isinstance(after, list) and after and dt._case_result(after[0], "CP1-T01") not in (None, "no result recorded yet"), after)
    check("[CP6-T17] the receipt reads line 1 unproven and names table toy and the wrong rows by id",
          "unproven" in seg.lower() and "toy" in seg and all(re.search(rf"\b{k}\b", seg) for k in ("tee", "diff", "sed_i", "crash")), seg)
    check("[CP6-T17] line 2 is unaffected", isinstance(after, list) and isinstance(before, list) and len(before) == 2
          and after[1] == before[1], (before[1:] if before else None, after[1:] if after else None))
    d2 = toy_a("t17b", rows=[BASE[0], BASE[4], CRASH_ROW], steps=steps)
    g.rx(d2, "shortcuts")
    t2 = dt._table(d2)
    check("[CP6-T17] an errored row alone also keeps the line unproven",
          isinstance(t2, list) and len(t2) == 2 and t2[0].get("verdict") == "unproven"
          and "crash" in json.dumps(t2[0]), t2)


def cp6_t18_linked_line_proven():
    """[CP6-T18] The same linked line is proven once every row is right on the current code."""
    steps = [[sc_case("CP1-T01", "toy", [1]), code_case("CP1-T02", [2])]]
    d = toy_a("t18", steps=steps, new=GUARD_GOOD)
    r = g.rx(d, "shortcuts")
    check("[CP6-T18] shortcuts exits 0", clean(r), out_of(r)[-500:])
    t = dt._table(d)
    out = out_of(g.rx(d, "receipt"))
    seg = _line_seg(out, df.DM2, 0)
    check("[CP6-T18] line 1 is proven in the table", isinstance(t, list) and t and t[0].get("verdict") == "proven", t)
    check("[CP6-T18] the receipt reads line 1 proven with the table named",
          "toy" in seg and re.search(r"(?<!un)proven", seg, re.I) is not None and "unproven" not in seg.lower(), seg)
    (d / "guard.py").write_text(GUARD_GOOD + "# edited again\n")
    t2 = dt._table(d)
    check("[CP6-T18] a stale saved result is not trusted: line 1 reads unproven",
          isinstance(t2, list) and t2 and t2[0].get("verdict") == "unproven", t2)
    g.rx(d, "shortcuts")
    t3 = dt._table(d)
    check("[CP6-T18] after `shortcuts` runs again line 1 is proven", isinstance(t3, list) and t3 and t3[0].get("verdict") == "proven", t3)
    rows = [BASE[0], BASE[3], BASE[4]]
    msteps = [[sc_case("CP1-T01", "toy", [1]), code_case("CP1-T02", [2])], [code_case("CP2-T01", [3])]]
    m, _ = m_project("t18m", "M", rows=rows, steps=msteps, strip_run_start=True, main_guard=GUARD_MAIN_DD)
    rj, j = scj(m)
    check("[CP6-T18] (main merged) every old side is unknown", all(x.get("verdict") == "unknown" for x in rows_of(j).values())
          and len(rows_of(j)) >= 3, verdicts(j))
    tm = dt._table(m)
    check("[CP6-T18] (main merged) an unknown old side does not keep the line unproven: line 1 is proven",
          isinstance(tm, list) and tm and tm[0].get("verdict") == "proven", tm)


def cp6_t19_plan_submit_checks_tables():
    """[CP6-T19] A plan whose shortcuts case names a table that does not exist is refused naming the case."""
    def submit(name, steps):
        d, run = df._init_project(name, extra_files=toy_files())
        df._write_plan(run, steps, df.DM2)
        return d, g.rx(d, "plan-submit")
    d, r = submit("t19a", [[code_case("CP1-T01", [1]), sc_case("CP1-T02", "nosuch", [2])]])
    out = out_of(r)
    check("[CP6-T19] a missing table is refused, naming CP1-T02 and the table",
          r.returncode != 0 and "CP1-T02" in out and "nosuch" in out and "Traceback" not in out, out[-700:])
    d, r = submit("t19b", [[code_case("CP1-T01", [1]), sc_case("CP1-T02", None, [2])]])
    out = out_of(r)
    check("[CP6-T19] a shortcuts case that names no table is refused, naming it",
          r.returncode != 0 and "CP1-T02" in out and "Traceback" not in out, out[-700:])
    d, r = submit("t19c", [[code_case("CP1-T01", [1]), sc_case("CP1-T02", "toy", [2])]])
    check("[CP6-T19] a valid toy shortcuts case is accepted", clean(r), out_of(r)[-700:])
    if clean(r):
        g.approve_plan(d)
        df._write_step_tests(d, 1, [code_case("CP1-T01", [1])])
        tw = g.rx(d, "tests-written", "CP1")
        check("[CP6-T19] tests-written does not demand the shortcuts case's id in a test file",
              clean(tw), out_of(tw)[-700:])


def cp6_t20_no_tables_and_legacy_runs():
    """[CP6-T20] A project with no tables, and a run approved before this change, keep working with a plain note."""
    def legacy(name, files):
        d, run = df._init_project(name, extra_files=files)
        (run / "tests/CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code"}]}))
        (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "checkpoints": [df._cp(1)]}))
        rp._legacy_submit(d)
        rp._legacy_approve(d)
        return d
    d = legacy("t20a", {"guard.py": GUARD_START})
    (d / "guard.py").write_text(GUARD_NEW)
    r = g.rx(d, "shortcuts")
    out = out_of(r)
    check("[CP6-T20] shortcuts exits 0 with one plain line saying there are no shortcut tables",
          clean(r) and re.search(r"no shortcut tables?", out, re.I) is not None, out[-500:])
    rj, j = scj(d)
    check("[CP6-T20] shortcuts --json exits 0 with no tables", clean(rj) and not tables_of(j), out_of(rj)[-300:])
    fin = df._build(d, 1, [{"id": "CP1-T01"}], report=[{"id": "CP1-T01", "status": "passed"}])
    check("[CP6-T20] the last step completes as before", clean(fin), out_of(fin)[-500:])
    s, rc = g.rx(d, "status"), g.rx(d, "receipt")
    check("[CP6-T20] status and receipt show no table block and no refusal",
          clean(s) and clean(rc) and SC_RE.search(out_of(s)) is None and SC_RE.search(out_of(rc)) is None,
          (out_of(s)[-300:], out_of(rc)[-300:]))
    d2 = legacy("t20b", toy_files())
    (d2 / "guard.py").write_text(GUARD_NEW)
    check("[CP6-T20] (second variant) the run has no run_start", "run_start" not in state(d2), state(d2).get("run_start"))
    r2, j2 = scj(d2)
    check("[CP6-T20] (second variant) the old side falls back to the step's base tree: tee is worse",
          clean(r2) and verdicts(j2) == BASE_VERDICTS, (out_of(r2)[-300:], verdicts(j2)))
    fin2 = df._build(d2, 1, [{"id": "CP1-T01"}], report=[{"id": "CP1-T01", "status": "passed"}])
    check("[CP6-T20] (second variant) complete exits 0, nothing is blocked", clean(fin2), out_of(fin2)[-500:])


def cp6_t21_unreadable_table():
    """[CP6-T21] A table file that is not valid JSON is reported by name and does not stop the other tables."""
    d = toy_a("t21", other=True)
    (d / "tests/shortcuts/broken.json").write_text("{ not json at all")
    r, j = scj(d)
    out = out_of(r)
    check("[CP6-T21] exits 0 with no traceback", clean(r) and j is not None, out[-500:])
    b = tbl(j, "broken")
    check("[CP6-T21] table 'broken' is reported unreadable with a plain reason",
          b is not None and b.get("ran") is False and re.search(r"unreadable|not valid|invalid", json.dumps(b), re.I) is not None, b)
    check("[CP6-T21] toy still runs with its normal verdicts", verdicts(j) == BASE_VERDICTS, verdicts(j))
    d2 = toy_a("t21b")
    (d2 / "tests/shortcuts/toy.json").write_text("{ broken")
    r2, j2 = scj(d2)
    p = out_of(g.rx(d2, "shortcuts"))
    check("[CP6-T21] a toy.json that was valid at the start: the old copy is used", verdicts(j2) == BASE_VERDICTS, verdicts(j2))
    check("[CP6-T21] the output says the current file is unreadable", re.search(r"unreadable", p, re.I) is not None, p[-600:])


def cp6_t22_main_merged_regression_still_shows():
    """[CP6-T22] After main is merged, this run's regression still shows and main's own changes are not blamed on it."""
    d, _ = m_project("t22", "M")
    r, j = scj(d)
    check("[CP6-T22] exits 0 with JSON", clean(r) and j is not None, out_of(r)[-500:])
    rows = rows_of(j)
    row_is("[CP6-T22]", rows, "tee", "REFUSED", "ALLOWED", "worse")
    row_is("[CP6-T22]", rows, "sed_i", "REFUSED", "REFUSED", "held")
    dd = rows.get("dd") or {}
    check("[CP6-T22] row dd (added by main) is still_open with new_row false",
          dd.get("verdict") == "still_open" and dd.get("new_row") is False and dd.get("old") == "ALLOWED"
          and dd.get("new") == "ALLOWED", dd)
    c = counts_of(j)
    check("[CP6-T22] counts are not inflated by main: 1 worse, 0 fixed, 2 still open, 4 held",
          c.get("worse") == 1 and c.get("fixed") == 0 and c.get("still_open") == 2 and c.get("held") == 4, c)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T22] plain output exits 0 and lists tee only under 'got worse'",
          kinds_of(out, C_TEE) == {"worse"} and kinds_of(out, C_SED) == set(), (kinds_of(out, C_TEE), kinds_of(out, C_SED)))


def cp6_t23_merge_keeps_run_start():
    """[CP6-T23] Merging main does not move where the run started."""
    d = df._approved("t23", steps=MSTEPS, dm=df.DM3, branch="lane", extra_files=toy_files())
    start = g.git(d, "rev-parse", "HEAD").stdout.strip()
    rs0 = state(d).get("run_start")
    check("[CP6-T23] plan approval wrote run_start with the starting commit and a tree id",
          isinstance(rs0, dict) and rs0.get("head") == start and bool(rs0.get("tree")), rs0)
    (d / "guard.py").write_text(GUARD_LANE)
    r = df._build(d, 1, MSTEPS[0], report=MPASS1)
    check("[CP6-T23] step 1 completes", clean(r), out_of(r)[-400:])
    check("[CP6-T23] run_start is unchanged after step 1", state(d).get("run_start") == rs0, state(d).get("run_start"))
    g.git(d, "checkout", "-q", "-b", "main", start)
    (d / "guard.py").write_text(GUARD_MAIN)
    _commit(d, "main change", "guard.py")
    g.git(d, "checkout", "-q", "lane")
    before_cp = state(d)["checkpoints"]["CP2"].get("base_head")
    rm = g.git(d, "merge", "--no-ff", "-q", "-m", "merge main", "main")
    check("[CP6-T23] (setup) git merge is clean", rm.returncode == 0, rm.stderr + rm.stdout)
    r1 = g.rx(d, "merged")
    st = state(d)
    head = g.git(d, "rev-parse", "HEAD").stdout.strip()
    check("[CP6-T23] merged is accepted", clean(r1), out_of(r1)[-400:])
    check("[CP6-T23] run_start is exactly what approval wrote after merged", st.get("run_start") == rs0, st.get("run_start"))
    check("[CP6-T23] the step's own base_head moved to the merge, so the two fields are separate",
          st["checkpoints"]["CP2"].get("base_head") == head and head != start and before_cp != head
          and st.get("run_start", {}).get("head") == start, (st["checkpoints"]["CP2"].get("base_head"), head))
    g.git(d, "checkout", "-q", "main")
    (d / "extra.txt").write_text("more from main\n")
    _commit(d, "main change 2", "extra.txt")
    g.git(d, "checkout", "-q", "lane")
    rm2 = g.git(d, "merge", "--no-ff", "-q", "-m", "merge main again", "main")
    g.rx(d, "merged")
    check("[CP6-T23] run_start is unchanged after a second merge of main",
          rm2.returncode == 0 and state(d).get("run_start") == rs0, state(d).get("run_start"))


def cp6_t24_main_only_change_does_not_trigger():
    """[CP6-T24] A guard change only main made does not trigger the table."""
    d, _ = m_project("t24", "M2")
    r, j = scj(d)
    check("[CP6-T24] exits 0", clean(r), out_of(r)[-400:])
    check("[CP6-T24] toy is not run: its watched files did not change in this run",
          (tbl(j) or {}).get("ran") is False, tables_of(j))
    fin = df._build(d, 2, MSTEPS[1], report=MPASS2)
    check("[CP6-T24] the last step completes", clean(fin), out_of(fin)[-500:])
    check("[CP6-T24] no result is saved for toy", not evidence(d).exists(), evidence(d))
    s = out_of(g.rx(d, "status"))
    check("[CP6-T24] the final review shows no table block", SC_RE.search(s) is None, s[-600:])

    (d / "guard.py").write_text(GUARD_MAIN.replace("    lambda c: 'tee' in c,\n", "    lambda c: 'wget' in c,\n"))
    r2, j2 = scj(d)
    check("[CP6-T24] once the lane edits guard.py the table does run", (tbl(j2) or {}).get("ran") is True, tables_of(j2))


def cp6_t25_older_run_after_merge_is_unknown():
    """[CP6-T25] An older run that has no start record and merged main gets a plain note, with no crash and no block."""
    d, _ = m_project("t25", "M", strip_run_start=True)
    check("[CP6-T25] (setup) the run has no run_start", "run_start" not in state(d), state(d).get("run_start"))
    r, j = scj(d)
    check("[CP6-T25] exits 0 with no traceback", clean(r) and j is not None, out_of(r)[-500:])
    rows = rows_of(j)
    check("[CP6-T25] every row has old 'unknown' and verdict 'unknown'",
          len(rows) == 7 and all(x.get("old") == "unknown" and x.get("verdict") == "unknown" for x in rows.values()), rows)
    check("[CP6-T25] the new-side verdicts are still shown",
          all(x.get("new") in ("ALLOWED", "REFUSED") for x in rows.values()), rows)
    check("[CP6-T25] counts.unknown equals the row count", counts_of(j).get("unknown") == len(rows) == 7, counts_of(j))
    note = re.compile(r"starting version[^\n]*(?:cannot|can't|could not|unknown)|(?:cannot|can't)[^\n]*starting version", re.I)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T25] one plain note says the starting version cannot be worked out", note.search(out) is not None, out[-700:])
    s, rc = out_of(g.rx(d, "status")), out_of(g.rx(d, "receipt"))
    fin = df._build(d, 2, MSTEPS[1], report=MPASS2)
    check("[CP6-T25] complete exits 0 and nothing is blocked", clean(fin), out_of(fin)[-500:])
    s, rc = out_of(g.rx(d, "status")), out_of(g.rx(d, "receipt"))
    check("[CP6-T25] status and receipt carry the note", note.search(s) is not None and note.search(rc) is not None,
          (s[-500:], rc[-500:]))


def cp6_t26_merge_tree_conflict_is_unknown():
    """[CP6-T26] A merge-tree conflict shows the old side as unknown instead of guessing."""
    d, _ = m_project("t26", "M3")
    rs = state(d).get("run_start")
    r, j = scj(d)
    check("[CP6-T26] exits 0 with JSON", clean(r) and j is not None, out_of(r)[-500:])
    rows = rows_of(j)
    check("[CP6-T26] every row has old 'unknown' and verdict 'unknown'",
          len(rows) == 6 and all(x.get("old") == "unknown" and x.get("verdict") == "unknown" for x in rows.values()), rows)
    check("[CP6-T26] the new-side verdicts are still shown", all(x.get("new") in ("ALLOWED", "REFUSED") for x in rows.values()), rows)
    out = out_of(g.rx(d, "shortcuts"))
    check("[CP6-T26] one plain note names the conflict", re.search(r"conflict", out, re.I) is not None, out[-700:])
    check("[CP6-T26] run_start is untouched", isinstance(rs, dict) and rs.get("head") and state(d).get("run_start") == rs, rs)


# ------------------------------------------------------------------------------------------------ runner

def write_report(ids):
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid in ids]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    tests = sorted((m.group(1).upper().replace("_", "-"), fn) for name, fn in globals().items()
                   if callable(fn) and (m := re.match(r"(cp6_[tx]\d+)_", name)))
    only = set(sys.argv[1:])
    for cid, fn in tests:
        if only and cid not in only:
            continue
        CASE_STATUS.setdefault(cid, True)
        print(f"[{cid}]")
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report([cid for cid, _ in tests if not only or cid in only])
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
