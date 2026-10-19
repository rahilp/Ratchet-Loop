#!/usr/bin/env python3
"""
CP7 tests: Ratchet's own guard gets a shortcut table.   python3 tests/test_shortcuts_guard.py

The shipped table is tests/shortcuts/guard.json and its runner tests/shortcuts/guard_runner.py (shape owned by CP6:
{name, watches, runner, rows[{id, command, expect}]}; runner exactly `python3 tests/shortcuts/guard_runner.py`;
`expect` exactly "allowed" or "refused"; the runner is called once per row with its version's folder as the working
folder and RATCHET_SHORTCUT_ROOT / RATCHET_SHORTCUT_COMMAND set, and prints exactly ALLOWED or REFUSED with exit 0;
anything else is an error). Each row also carries a "source": "director probe", "dogfood log" or
"<lane C test file path> <case id>".

Contract this file pins for the build (so the runner and the rows agree):
  - rows name only the runs `hk` and `other` under `.ratchet/runs/`;
  - the runner builds its own throwaway project (under the TMPDIR it is given) holding `.ratchet/active.json`,
    `.ratchet/runs/hk/state.json` and `.ratchet/runs/other/`, calls the version's `_guard_shell(cmd, cwd)` there
    (cmd = the row's command, never executed), prints ALLOWED when it returns None and REFUSED when it returns
    text, and removes the project before it exits;
  - an exception from the guard, a version with no `_guard_shell`, a ratchet.py that cannot be loaded and a version
    folder that does not exist are errors: non-zero exit, a plain message on stderr, no ALLOWED/REFUSED line.

The planned CP7 cases assumed the runner prints one line per row; CP6's real runner contract is one call per row,
so cases T04 to T08 drive the runner once per row, the way ratchet.py does. Row ids replace the planned row "name",
and the table's expectations are lowercase (CP6's allowlist).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP7-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                      # noqa: E402
import test_gates as g           # noqa: E402
import test_dm_finish as df      # noqa: E402

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP7-[TX]\d+[a-z]?)\]")

ENGINE_REL = "skills/ratchet-orchestrator/scripts/ratchet.py"
TABLE_REL = "tests/shortcuts/guard.json"
RUNNER_REL = "tests/shortcuts/guard_runner.py"
RUNNER_CMD = "python3 tests/shortcuts/guard_runner.py"
LANE_C_FILES = ("tests/test_guard_writes.py", "tests/test_guard_reads.py", "tests/test_guard_python_reads.py",
                "tests/test_guard_reserved_names.py")


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


# ------------------------------------------------------------------------------------------------ pinned shortcuts

S = ".ratchet/runs/hk/state.json"
# The director's shortcut probe (copied; the probe file lives in a scratchpad that may be gone).
PROBE_WRITES = {
    "plain redirect": f"echo x > {S}",
    "quoted redirect target": f"jq . /tmp/s > \"{S}\"",
    "1> redirect": f"cat x 1> {S}",
    "2> redirect": f"cat /tmp/new.json 2> {S}",
    "&> redirect": f"jq . /tmp/new.json &> {S}",
    "tee": f"cat /tmp/n | tee {S}",
    "process subst tee": f"cat README.md > >(tee {S})",
    "uniq stdin operand": f"cat /tmp/new | uniq - {S}",
    "cp -t": f"cp -t .ratchet/runs/hk /tmp/state.json",
    "cp -rt combined": f"cp -rt .ratchet/runs/hk /tmp/state.json",
    "while read loop": "find .ratchet/runs -name state.json | while read f; do echo x > \"$f\"; done",
    "sed -i": f"sed -i s/a/b/ {S}",
    "python write": f"python3 -c \"open('{S}','w').write('x')\"",
}
# Everyday read-only commands (director probe READ row and the dogfood log's harmless commands).
EVERYDAY_READS = {
    "cat": f"cat {S}",
    "grep evidence": "grep -r PASS .ratchet/runs/hk/evidence",
    "ls run folder": "ls -la .ratchet/runs/hk",
    "diff": f"diff {S} /tmp/state.copy.json",
    "test -f": f"test -f {S}",
    "python read": f"python3 -c \"print(open('{S}').read())\"",
    "heredoc to /tmp": "cat > /tmp/note.md <<'EOF'\nthe phase is in .ratchet/runs/hk/state.json\nEOF",
}


def _is_write_shape(c):
    return bool(re.search(r"[^0-9&>]>\s*\"?\.ratchet/|\s1>\s|\s2>\s|\s&>\s|\|\s*tee\s|>\(tee |uniq - |\bcp -r?t\b|"
                          r"while read|sed -i|python3 -c .*open\(.*,\s*'w'", c))


WRITE_SHAPES = {
    "plain redirect": lambda c: re.search(r"^echo \S+ > \.ratchet/runs/\w+/state\.json\s*$", c),
    "quoted redirect target": lambda c: re.search(r">\s*\"\.ratchet/runs/\w+/state\.json\"", c),
    "1> redirect": lambda c: re.search(r"\s1>\s", c),
    "2> redirect": lambda c: re.search(r"\s2>\s", c),
    "&> redirect": lambda c: re.search(r"\s&>\s", c),
    "tee": lambda c: re.search(r"\|\s*tee\s+\S*state\.json", c),
    "process subst tee": lambda c: re.search(r">\s*>\(tee ", c),
    "uniq stdin operand": lambda c: re.search(r"uniq - ", c),
    "cp -t": lambda c: re.search(r"\bcp -r?t\b", c),
    "while read loop": lambda c: re.search(r"while read", c),
    "sed -i": lambda c: re.search(r"sed -i", c),
    "python write": lambda c: re.search(r"python3 -c .*open\(.*,\s*'w'", c),
}
READ_SHAPES = {
    "cat": lambda c: re.match(r"cat \S*\.ratchet/runs/\w+/\S*\.json\s*$", c),
    "grep evidence": lambda c: re.match(r"grep .*\.ratchet/runs/\w+/evidence", c),
    "ls run folder": lambda c: re.match(r"ls .*\.ratchet/runs/\w+", c),
    "diff": lambda c: re.match(r"diff .*\.ratchet/runs/", c),
    "test -f": lambda c: re.match(r"test -f .*\.ratchet/runs/", c),
    "python read": lambda c: (re.match(r"python3 -c", c) and "open(" in c and ".ratchet/runs/" in c
                              and not re.search(r",\s*['\"][wax]", c) and ".write" not in c),
    "heredoc to /tmp": lambda c: "<<" in c and "/tmp/" in c and ".ratchet/runs/" in c,
}
for _k, _c in PROBE_WRITES.items():  # self-check of the shapes against the pinned literals
    if _k in WRITE_SHAPES:
        assert WRITE_SHAPES[_k](_c), (_k, _c)
for _k, _c in EVERYDAY_READS.items():
    assert READ_SHAPES[_k](_c), (_k, _c)


# ------------------------------------------------------------------------------------------------ table + runner

def load_table():
    p = ROOT / TABLE_REL
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def rows_of_table():
    t = load_table() or {}
    rows = t.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def argv_of(root):
    return [sys.executable, "./" + RUNNER_CMD.split(" ", 1)[1]]


def run_row(root, command, cwd=None, tmpdir=None, extra_env=None):
    """One runner call, the way ratchet.py makes it. A missing runner is a plain failed result, not a crash."""
    cwd = Path(cwd or root)
    if not (cwd / RUNNER_REL).exists():
        return subprocess.CompletedProcess([], 127, "", "runner file is missing")
    env = dict(os.environ, RATCHET_SHORTCUT_ROOT=str(root), RATCHET_SHORTCUT_COMMAND=command,
               PYTHONDONTWRITEBYTECODE="1")
    if tmpdir:
        env["TMPDIR"] = str(tmpdir)
    env.update(extra_env or {})
    try:
        return subprocess.run(argv_of(root), cwd=cwd, capture_output=True, text=True, env=env, timeout=120)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([], 124, "", "timed out")


def answer(r):
    """ALLOWED | REFUSED when the call is a clean answer, else 'error'."""
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out in ("ALLOWED", "REFUSED") else "error"


def fresh_tmp():
    return Path(_tmp.mkdtemp(prefix="ratchet-cp7-tmp-"))


_DIRECT: dict = {}


def direct_answers():
    """{row id: ALLOWED|REFUSED|error} from the shipped runner on the repo's own engine (computed once)."""
    if "v" not in _DIRECT:
        res = {}
        for r in rows_of_table():
            res[r.get("id")] = answer(run_row(ROOT, r.get("command", ""), tmpdir=fresh_tmp()))
        _DIRECT["v"] = res
    return _DIRECT["v"]


def verdict(expect, old, new):
    want = {"refused": "REFUSED", "allowed": "ALLOWED"}.get(expect)
    if want is None or "error" in (old, new):
        return "errored"
    if old == want:
        return "held" if new == want else "worse"
    return "fixed" if new == want else "still_open"


def toy_root(engine_src, with_runner=True):
    d = Path(_tmp.mkdtemp(prefix="ratchet-cp7-toy-"))
    if engine_src is not None:
        p = d / ENGINE_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(engine_src)
    if with_runner:
        (d / "tests/shortcuts").mkdir(parents=True, exist_ok=True)
        if (ROOT / RUNNER_REL).exists():
            shutil.copy(ROOT / RUNNER_REL, d / RUNNER_REL)
    return d


ENGINE_A = "def _guard_shell(cmd, cwd, who=None):\n    return None\n"
ENGINE_B = "def _guard_shell(cmd, cwd, who=None):\n    return 'RATCHET GUARD: refused by the toy'\n"
ENGINE_C = ("def _guard_shell(cmd, cwd, who=None):\n    if '&>' in cmd:\n"
            "        raise RuntimeError('toy guard exploded on &>')\n    return 'RATCHET GUARD: refused'\n")
ENGINE_D = "X = 1\n"
ENGINE_E = "def _guard_shell(cmd, cwd, who=None:\n    return None\n"
ENGINE_F = '''import hashlib, json, os, re
def _sha(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError:
        return None
def _guard_shell(cmd, cwd, who=None):
    names = sorted(set(re.findall(r"\\.ratchet/runs/([A-Za-z0-9_-]+)", cmd)))
    runs = os.path.join(cwd, ".ratchet", "runs")
    states = {n: _sha(os.path.join(runs, n, "state.json")) for n in (os.listdir(runs) if os.path.isdir(runs) else [])}
    rec = {"cmd": cmd, "cwd": cwd, "active": os.path.exists(os.path.join(cwd, ".ratchet", "active.json")),
           "named": {n: os.path.isdir(os.path.join(runs, n)) for n in names}, "states": states}
    with open(os.environ["TOY_RECORD"], "a") as f:
        f.write(json.dumps(rec) + "\\n")
    return "RATCHET GUARD: refused by the toy"
'''


# ------------------------------------------------------------------------------------------------ cases

def cp7_t01_probe_shortcuts_are_should_refuse_rows():
    """[CP7-T01] Every shortcut the director's probe tried is in the table as should-refuse."""
    rows = rows_of_table()
    check("[CP7-T01] the table tests/shortcuts/guard.json exists and has rows", bool(rows), load_table())
    for label, shape in WRITE_SHAPES.items():
        hit = [r for r in rows if isinstance(r.get("command"), str) and shape(r["command"])
               and r.get("source") in ("director probe", "dogfood log")]
        check(f"[CP7-T01] a row for '{label}' exists", bool(hit), None)
        check(f"[CP7-T01] every '{label}' row expects refused", bool(hit) and all(r.get("expect") == "refused" for r in hit),
              [(r.get("id"), r.get("expect")) for r in hit])
        check(f"[CP7-T01] every '{label}' row aims at a run's state.json under .ratchet/runs/",
              bool(hit) and all(re.search(r"\.ratchet/runs\b", r["command"]) and "state.json" in r["command"] for r in hit),
              [r.get("command") for r in hit])
    bad = [r.get("id") for r in rows if isinstance(r.get("command"), str) and _is_write_shape(r["command"])
           and r.get("source") in ("director probe", "dogfood log") and r.get("expect") != "refused"]
    check("[CP7-T01] not one write-shaped row from the director probe or dogfood log expects allowed", bool(rows) and bad == [], bad)


def cp7_t02_everyday_reads_are_should_allow_rows():
    """[CP7-T02] Everyday read-only commands are in the table as should-allow."""
    rows = rows_of_table()
    for label, shape in READ_SHAPES.items():
        hit = [r for r in rows if isinstance(r.get("command"), str) and shape(r["command"])
               and r.get("source") in ("director probe", "dogfood log")]
        check(f"[CP7-T02] a row for the read '{label}' exists", bool(hit), None)
        check(f"[CP7-T02] every '{label}' read row expects allowed", bool(hit) and all(r.get("expect") == "allowed" for r in hit),
              [(r.get("id"), r.get("expect")) for r in hit])
    nref = sum(1 for r in rows if r.get("expect") == "refused")
    nall = sum(1 for r in rows if r.get("expect") == "allowed")
    check("[CP7-T02] the table holds at least 12 should-refuse rows", nref >= 12, nref)
    check("[CP7-T02] the table holds at least 7 should-allow rows", nall >= 7, nall)


def cp7_t03_table_is_well_formed():
    """[CP7-T03] The table is well-formed and watches the engine file."""
    t = load_table()
    check("[CP7-T03] the table is valid JSON (an object)", t is not None, t)
    t = t or {}
    rows = rows_of_table()
    check("[CP7-T03] the table is named guard", t.get("name") == "guard", t.get("name"))
    check("[CP7-T03] the runner is exactly 'python3 tests/shortcuts/guard_runner.py'", t.get("runner") == RUNNER_CMD,
          t.get("runner"))
    check("[CP7-T03] the runner file exists", (ROOT / RUNNER_REL).is_file(), RUNNER_REL)
    watches = t.get("watches") if isinstance(t.get("watches"), list) else []
    check("[CP7-T03] it watches the engine file", ENGINE_REL in watches, watches)
    canon = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*/?$")
    check("[CP7-T03] every watched path is canonical and exists in the repo",
          bool(watches) and all(isinstance(w, str) and canon.match(w) and ".." not in w.split("/")
                                and (ROOT / w.rstrip("/")).exists() for w in watches), watches)
    ids = [r.get("id") for r in rows]
    cmds = [r.get("command") for r in rows]
    check("[CP7-T03] the table has rows", len(rows) >= 19, len(rows))
    check("[CP7-T03] every row id is a non-empty string and the ids are unique",
          bool(rows) and all(isinstance(i, str) and i.strip() for i in ids) and len(set(ids)) == len(ids), ids)
    check("[CP7-T03] every command is a non-empty string and no two rows share one",
          bool(rows) and all(isinstance(c, str) and c.strip() for c in cmds) and len(set(cmds)) == len(cmds), cmds)
    check("[CP7-T03] every expectation is exactly 'allowed' or 'refused'",
          bool(rows) and all(r.get("expect") in ("allowed", "refused") for r in rows), [r.get("expect") for r in rows])
    check("[CP7-T03] every row has a non-empty source", bool(rows) and all(isinstance(r.get("source"), str) and r["source"].strip()
                                                           for r in rows), [r.get("source") for r in rows])
    named = {n for c in cmds if isinstance(c, str) for n in re.findall(r"\.ratchet/runs/([A-Za-z0-9_-]+)", c)}
    check("[CP7-T03] rows name only the runs hk and other", bool(rows) and named <= {"hk", "other"}, named)


def cp7_t04_real_engine_answers_every_row():
    """[CP7-T04] Running the table on the current engine gives an answer for every row and no errors."""
    rows = rows_of_table()
    check("[CP7-T04] there are rows to run", bool(rows), None)
    bad, leftovers, errs = [], [], []
    for r in rows:
        tmp = fresh_tmp()
        p = run_row(ROOT, r.get("command", ""), tmpdir=tmp)
        if answer(p) == "error" or p.stderr.strip() or len(p.stdout.strip().splitlines()) != 1:
            bad.append((r.get("id"), p.returncode, p.stdout[-80:], p.stderr[-120:]))
        left = list(tmp.iterdir())
        if left:
            leftovers.append((r.get("id"), [x.name for x in left]))
        errs.append(answer(p))
    check("[CP7-T04] every row exits 0 with exactly one ALLOWED or REFUSED line and empty stderr",
          bool(rows) and bad == [], bad[:5])
    check("[CP7-T04] ALLOWED plus REFUSED answers equal the row count, no error among them",
          bool(rows) and len(errs) == len(rows) and "error" not in errs, errs)
    check("[CP7-T04] no throwaway project is left in the temp area", bool(rows) and leftovers == [], leftovers[:5])


def _all_answers(root, rows, tmp=None, **kw):
    return [(r.get("id"), run_row(root, r.get("command", ""), tmpdir=tmp or fresh_tmp(), **kw)) for r in rows]


def cp7_t05_runner_reports_the_guards_answer():
    """[CP7-T05] The runner reports what the guard really says, not what the table expects."""
    rows = rows_of_table()
    check("[CP7-T05] there are rows to run", bool(rows), None)
    a = [(i, answer(p)) for i, p in _all_answers(toy_root(ENGINE_A), rows)]
    b = [(i, answer(p)) for i, p in _all_answers(toy_root(ENGINE_B), rows)]
    check("[CP7-T05] against the allow-everything guard every row prints ALLOWED, write rows included",
          bool(rows) and all(x == "ALLOWED" for _, x in a), [t for t in a if t[1] != "ALLOWED"][:5])
    check("[CP7-T05] against the refuse-everything guard every row prints REFUSED, read rows included",
          bool(rows) and all(x == "REFUSED" for _, x in b), [t for t in b if t[1] != "REFUSED"][:5])
    check("[CP7-T05] both runs cover the same rows in the same order as the table",
          [i for i, _ in a] == [r.get("id") for r in rows] == [i for i, _ in b] and bool(rows), None)


def cp7_t06_crashing_guard_is_an_error():
    """[CP7-T06] A guard that crashes on a row shows that row as an error, never as refused."""
    rows = rows_of_table()
    root = toy_root(ENGINE_C)
    amp = [r for r in rows if "&>" in str(r.get("command"))]
    check("[CP7-T06] the table has a row with &>", bool(amp), None)
    res = dict((i, p) for i, p in _all_answers(root, rows))
    for r in amp:
        p = res[r["id"]]
        out = (p.stdout or "")
        check(f"[CP7-T06] the &> row {r['id']} is an error: non-zero exit, no ALLOWED or REFUSED printed",
              p.returncode != 0 and "ALLOWED" not in out and "REFUSED" not in out, (p.returncode, out[-80:]))
        check(f"[CP7-T06] the &> row {r['id']} says what went wrong on stderr", "exploded" in (p.stderr or ""),
              (p.stderr or "")[-200:])
    others = [r for r in rows if r not in amp]
    check("[CP7-T06] every other row still prints REFUSED with exit 0 (the run goes on after a crash)",
          bool(others) and all(answer(res[r["id"]]) == "REFUSED" for r in others),
          [r["id"] for r in others if answer(res[r["id"]]) != "REFUSED"][:5])


def cp7_t07_no_usable_guard_is_an_error():
    """[CP7-T07] A version with no usable guard function is an error for the whole table, not a pass."""
    rows = rows_of_table()
    sample = [rows[0], rows[len(rows) // 2], rows[-1]] if rows else []
    check("[CP7-T07] there are rows to run", bool(sample), None)
    cases = [("no _guard_shell", toy_root(ENGINE_D), r"_guard_shell|guard function|no guard"),
             ("syntax error", toy_root(ENGINE_E), r"cannot load|could not load|syntax|can't load"),
             ("no such version", None, r"no such|not exist|does not exist|cannot find|not found|missing")]
    for label, root, why_re in cases:
        tmp = fresh_tmp()
        if root is None:
            host = toy_root(None)
            root_arg = Path(tempfile.gettempdir()) / "ratchet-cp7-no-such-version"
            results = [run_row(root_arg, r["command"], cwd=host, tmpdir=tmp) for r in sample]
        else:
            results = [run_row(root, r["command"], tmpdir=tmp) for r in sample]
        check(f"[CP7-T07] ({label}) every call exits non-zero and prints no ALLOWED or REFUSED",
              bool(sample) and all(p.returncode != 0 and "ALLOWED" not in p.stdout and "REFUSED" not in p.stdout
                                   for p in results), [(p.returncode, p.stdout[-60:]) for p in results])
        check(f"[CP7-T07] ({label}) stderr names what is wrong in plain words",
              bool(sample) and all(re.search(why_re, p.stderr or "", re.I) and "Traceback" not in (p.stderr or "")
                                   for p in results), [(p.stderr or "")[-200:] for p in results][:1])
        check(f"[CP7-T07] ({label}) no throwaway project is left behind", bool(sample) and list(tmp.iterdir()) == [],
              [x.name for x in tmp.iterdir()])


def _listing(p):
    p = Path(p)
    return sorted(str(x.relative_to(p)) for x in p.rglob("*")) if p.exists() else None


def cp7_t08_runner_builds_its_own_project():
    """[CP7-T08] The runner builds its own throwaway project and never runs a row's command or touches the real one."""
    rows = rows_of_table()
    root = toy_root(ENGINE_F)
    engine_hash = hashlib.sha256((root / ENGINE_REL).read_bytes()).hexdigest()
    before = _listing(ROOT / ".ratchet")
    rec = Path(_tmp.mkdtemp(prefix="ratchet-cp7-rec-")) / "rec.jsonl"
    tmp = fresh_tmp()
    results = [run_row(root, r["command"], tmpdir=tmp, extra_env={"TOY_RECORD": str(rec)}) for r in rows]
    calls = [json.loads(x) for x in rec.read_text().splitlines()] if rec.exists() else []
    check("[CP7-T08] the guard was called once per row", bool(rows) and len(calls) == len(rows) == len(results),
          (len(calls), len(rows), [(p.returncode, p.stderr[-100:]) for p in results[:2]]))
    check("[CP7-T08] every call got the row's own command, unchanged",
          [c["cmd"] for c in calls] == [r["command"] for r in rows] and bool(rows), None)
    check("[CP7-T08] every cwd is a folder outside the repo checkout and outside the version folder",
          bool(calls) and all(not str(Path(c["cwd"]).resolve()).startswith(str(ROOT.resolve()))
                              and not str(Path(c["cwd"]).resolve()).startswith(str(root.resolve())) for c in calls),
          [c["cwd"] for c in calls][:2])
    check("[CP7-T08] at each call the project has .ratchet/active.json and the runs the row names",
          bool(calls) and all(c["active"] and all(c["named"].values()) for c in calls)
          and any(c["named"] for c in calls), [c for c in calls if not (c["active"] and all(c["named"].values()))][:1])
    check("[CP7-T08] every call sees a state.json for run hk", bool(calls) and all("hk" in c["states"] and c["states"]["hk"]
                                                                                    for c in calls), calls[:1])
    check("[CP7-T08] the state.json hash is the same on every call (no row's command was run)",
          bool(calls) and len({json.dumps(c["states"], sort_keys=True) for c in calls}) == 1,
          len({json.dumps(c["states"], sort_keys=True) for c in calls}))
    check("[CP7-T08] each throwaway project is gone afterwards", bool(calls) and not any(Path(c["cwd"]).exists() for c in calls),
          [c["cwd"] for c in calls if Path(c["cwd"]).exists()][:2])
    check("[CP7-T08] the engine file and the real repo's .ratchet/ listing are unchanged",
          hashlib.sha256((root / ENGINE_REL).read_bytes()).hexdigest() == engine_hash
          and _listing(ROOT / ".ratchet") == before and bool(rows), None)


# ------------------------------------------------------------------------------------------------ guard-proj

def _watched_files():
    t = load_table() or {}
    out = {}
    for w in (t.get("watches") if isinstance(t.get("watches"), list) else []):
        p = ROOT / str(w).rstrip("/")
        if p.is_file():
            out[str(w)] = p.read_text()
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and "__pycache__" not in f.parts:
                    out[str(f.relative_to(ROOT))] = f.read_text(errors="replace")
    return out


def guard_files(engine_text=ENGINE_A):
    files = _watched_files()
    files[ENGINE_REL] = engine_text
    for rel in (TABLE_REL, RUNNER_REL):
        p = ROOT / rel
        files[rel] = p.read_text() if p.exists() else ""
    files["README.md"] = "# guard-proj\n"
    return files


def real_engine_text():
    return (ROOT / ENGINE_REL).read_text()


def scj(d, *extra):
    r = g.rx(d, "shortcuts", *extra, "--json")
    try:
        return r, json.loads(r.stdout)
    except Exception:
        return r, None


def guard_table(j):
    for t in ((j or {}).get("tables") or []) if isinstance(j, dict) else []:
        if isinstance(t, dict) and t.get("name") == "guard":
            return t
    return None


def expected_verdicts():
    """Each row's verdict if the start is toy engine A (ALLOWED always) and now is the repo's engine, computed
    from the direct runner run, never hard-coded."""
    cur = direct_answers()
    return {r["id"]: verdict(r.get("expect"), "ALLOWED", cur.get(r["id"], "error")) for r in rows_of_table()}


def counts_of_verdicts(v):
    c = {k: 0 for k in ("worse", "still_open", "fixed", "held", "errored")}
    for x in v.values():
        c[x] = c.get(x, 0) + 1
    return c


def cp7_t09_wrong_rows_show_as_still_open():
    """[CP7-T09] Rows the engine answers wrongly show as still open, never fixed, dropped or flipped."""
    rows = rows_of_table()
    check("[CP7-T09] the shipped table has rows", bool(rows), None)
    if not rows:
        return
    d = df._approved("cp7-t09", extra_files=guard_files())
    (d / ENGINE_REL).write_text(real_engine_text())
    r, j = scj(d)
    t = guard_table(j)
    check("[CP7-T09] shortcuts --json exits 0 and holds the guard table, run", r.returncode == 0 and t is not None
          and t.get("ran") is True, (r.stdout[-300:], r.stderr[-300:]))
    t = t or {}
    got = [x.get("id") for x in t.get("rows") or []]
    check("[CP7-T09] every table row appears exactly once", sorted(got) == sorted(x["id"] for x in rows), got)
    exp = expected_verdicts()
    have = {x.get("id"): x.get("verdict") for x in t.get("rows") or []}
    check("[CP7-T09] every row's verdict equals the one computed from the direct runner run", have == exp,
          {k: (have.get(k), v) for k, v in exp.items() if have.get(k) != v})
    still = sorted(k for k, v in exp.items() if v == "still_open")
    check("[CP7-T09] the still-open rows are exactly the rows the engine answers wrongly (never fixed)",
          sorted(k for k, v in have.items() if v == "still_open") == still
          and not [k for k in still if have.get(k) == "fixed"], still)
    c = t.get("counts") or {}
    want = counts_of_verdicts(exp)
    check("[CP7-T09] the counts match and add up to the row count, errored is 0",
          all(c.get(k) == n for k, n in want.items()) and c.get("errored") == 0
          and sum(c.get(k, 0) for k in want) == len(rows), (c, want))
    out = (g.rx(d, "shortcuts").stdout or "")
    check("[CP7-T09] the plain output names each still-open row under 'still open'",
          all(k in out for k in still) and (not still or re.search(r"still open", out, re.I) is not None), out[-500:])


_FIN: dict = {}


def finished(kind):
    """(project, complete's result): the engine-editing run ('engine'), the README-only run ('readme')."""
    if kind not in _FIN:
        d = df._approved(f"cp7-fin-{kind}", extra_files=guard_files())
        if kind == "engine":
            (d / ENGINE_REL).write_text(real_engine_text())
        else:
            (d / "README.md").write_text("# guard-proj\n\nmore words\n")
        _FIN[kind] = (d, df._build(d, 1, df.FIN2_STEPS[0], report=df.PASSED2))
    return _FIN[kind]


def _plain(r):
    return (r.stdout or "") + (r.stderr or "")


def cp7_t10_engine_change_shows_the_table_at_final_review():
    """[CP7-T10] A run that changes the engine shows the guard table at its final review."""
    check("[CP7-T10] the shipped table exists to copy into the project", (ROOT / TABLE_REL).exists(), TABLE_REL)
    if not (ROOT / TABLE_REL).exists():
        return
    d, r = finished("engine")
    check("[CP7-T10] the last step completes", r is not None and r.returncode == 0 and "Traceback" not in _plain(r),
          _plain(r)[-400:] if r else None)
    exp = expected_verdicts()
    c = counts_of_verdicts(exp)
    still = sorted(k for k, v in exp.items() if v == "still_open")
    s = _plain(g.rx(d, "status"))
    rc = _plain(g.rx(d, "receipt"))
    for where, text in (("status (final review)", s), ("receipt", rc)):
        check(f"[CP7-T10] the {where} names the guard table", re.search(r"shortcut table", text, re.I) is not None
              and re.search(r"\bguard\b", text, re.I) is not None, text[-600:])
        count_ok = all(re.search(rf"\b{n}\s+{w}\b", text, re.I) for n, w in
                       ((c["worse"], "worse"), (c["still_open"], "still open"), (c["fixed"], "fixed"),
                        (c["errored"], "errored")))
        check(f"[CP7-T10] the {where} shows the worse, still open, fixed and errored counts", count_ok, (c, text[-700:]))
        check(f"[CP7-T10] the {where} names every still-open row", all(k in text for k in still), still)
    ev = Path(d) / ".ratchet/runs/f/evidence/shortcuts/guard.json"
    check("[CP7-T10] the result was saved without anyone asking for it", ev.exists(), ev)


def cp7_t11_readme_only_run_does_not_show_the_table():
    """[CP7-T11] A run that does not touch the engine does not show the guard table."""
    check("[CP7-T11] the shipped table exists, so the project holds it", (ROOT / TABLE_REL).exists(), TABLE_REL)
    if not (ROOT / TABLE_REL).exists():
        return
    d, r = finished("readme")
    check("[CP7-T11] the last step completes cleanly", r is not None and r.returncode == 0
          and "Traceback" not in _plain(r), _plain(r)[-400:] if r else None)
    s, rc = _plain(g.rx(d, "status")), _plain(g.rx(d, "receipt"))
    pat = re.compile(r"shortcut table|guard\.json|guard table", re.I)
    check("[CP7-T11] neither the final review nor the receipt mentions the guard table",
          pat.search(s) is None and pat.search(rc) is None, (s[-300:], rc[-300:]))
    check("[CP7-T11] no shortcut-table result is saved",
          not (Path(d) / ".ratchet/runs/f/evidence/shortcuts/guard.json").exists(), None)
    check("[CP7-T11] status and receipt show no error", g.rx(d, "status").returncode == 0
          and g.rx(d, "receipt").returncode == 0 and "Traceback" not in s + rc, None)


def cp7_t12_project_without_tables_is_unaffected():
    """[CP7-T12] A project with no shortcut tables is unaffected by a change to a file named like the engine."""
    d = df._approved("cp7-t12", extra_files={ENGINE_REL: "# old engine stand-in\n"})
    (d / ENGINE_REL).write_text("# edited engine stand-in\n")
    r = df._build(d, 1, df.FIN2_STEPS[0], report=df.PASSED2)
    s, rc = g.rx(d, "status"), g.rx(d, "receipt")
    pat = re.compile(r"shortcut table|guard\.json|guard table", re.I)
    check("[CP7-T12] the run finishes normally, no traceback",
          r.returncode == 0 and s.returncode == 0 and rc.returncode == 0
          and "Traceback" not in _plain(r) + _plain(s) + _plain(rc), _plain(r)[-300:])
    check("[CP7-T12] no guard table in the final review or receipt", pat.search(_plain(s)) is None
          and pat.search(_plain(rc)) is None, _plain(s)[-300:])
    check("[CP7-T12] the saved evidence has no shortcut-table result",
          not (Path(d) / ".ratchet/runs/f/evidence/shortcuts").exists(), None)


# ------------------------------------------------------------------------------------------------ docs

def _windows(text, pat, width=1600):
    out = []
    for m in re.finditer(pat, text, re.I):
        out.append(text[max(0, m.start() - width // 4): m.end() + width])
    return "\n".join(out)


NAME_PAT = r"tests/shortcuts/guard\.json|guard (?:shortcut )?table|shortcut table for (?:ratchet's own |the )?guard"


def _doc_checks(label, text, agent=False):
    w = _windows(text, NAME_PAT)
    check(f"[CP7-T13] {label} names the guard shortcut table", w != "", None)
    check(f"[CP7-T13] {label} says it is a fixed list of shortcut commands the guard should refuse next to everyday "
          "read-only commands it should allow",
          re.search(r"fixed (?:list|table|set)", w, re.I) is not None and re.search(r"refus", w, re.I) is not None
          and re.search(r"everyday|read-only|reads?\b", w, re.I) is not None and re.search(r"allow", w, re.I) is not None,
          w[:300])
    check(f"[CP7-T13] {label} says a run that changes the engine shows the result at the final review",
          re.search(r"(changes?|edits?|touch\w*)\s+(?:the\s+)?(?:engine|ratchet\.py|guard)", w, re.I) is not None
          and re.search(r"final review", w, re.I) is not None, w[:300])
    check(f"[CP7-T13] {label} says rows are only ever added (never removed, reworded or flipped; a still-open row stays listed)",
          re.search(r"only (?:ever )?(?:added|grows?)|rows? (?:are|is) (?:only )?(?:ever )?added|add(?:ed)? (?:rows )?only|never "
                    r"(?:removed|dropped)", w, re.I) is not None
          and re.search(r"reword|flip", w, re.I) is not None and re.search(r"still[- ]open", w, re.I) is not None, w[:300])
    if agent:
        check(f"[CP7-T13] {label} speaks to the agent (what to show or do), not to a user typing a command",
              re.search(r"\byou\b|the agent|show the user|\$RATCHET", w) is not None
              and re.search(r"\byou (?:can )?type\b|\btype `?ratchet", w, re.I) is None, w[:300])


def cp7_t13_docs_explain_the_guard_table():
    """[CP7-T13] The docs tell agents what the guard table is and that rows are only ever added."""
    readme = (ROOT / "README.md").read_text()
    skill = (ROOT / "skills/ratchet-orchestrator/SKILL.md").read_text()
    schemas = (ROOT / "skills/ratchet-orchestrator/references/schemas.md").read_text()
    _doc_checks("README.md", readme)
    _doc_checks("SKILL.md", skill, agent=True)
    m = re.search(r"^## 14\. Shortcut tables.*?(?=^## \d+\.|\Z)", schemas, re.S | re.M)
    sec = m.group(0) if m else ""
    check("[CP7-T13] schemas.md lists the shipped guard table and its runner in the shortcut-table section",
          "tests/shortcuts/guard.json" in sec and "tests/shortcuts/guard_runner.py" in sec, sec[:200])


# ------------------------------------------------------------------------------------------------ sources

SOURCE_RE = re.compile(r"^(?:director probe|dogfood log|(" + "|".join(re.escape(f) for f in LANE_C_FILES)
                       + r") (CP[1-4]-[TX]\d+))$")


def cp7_t14_every_row_says_where_it_came_from():
    """[CP7-T14] Every row says where it came from, and the guard tests' commands are in the table."""
    rows = rows_of_table()
    check("[CP7-T14] the table has rows", bool(rows), None)
    bad, missing, used = [], [], {}
    for r in rows:
        src = r.get("source")
        m = SOURCE_RE.match(src) if isinstance(src, str) else None
        if not m:
            bad.append((r.get("id"), src))
            continue
        if m.group(1):
            used.setdefault(m.group(1), []).append(r)
            p = ROOT / m.group(1)
            if not p.is_file() or not re.search(r"\[" + re.escape(m.group(2)) + r"\]|\b" + re.escape(m.group(2).lower())
                                                + r"_", p.read_text()):
                missing.append((r.get("id"), src))
    check("[CP7-T14] every source is 'director probe', 'dogfood log' or '<lane C test file> <case id>'",
          bool(rows) and bad == [], bad[:5])
    check("[CP7-T14] for a test-file source the file exists and holds that case id", bool(rows) and missing == [],
          missing[:5])
    check("[CP7-T14] each of the four lane C test files is the source of at least one row",
          all(f in used for f in LANE_C_FILES), [f for f in LANE_C_FILES if f not in used])
    wr = used.get("tests/test_guard_writes.py", []) + used.get("tests/test_guard_reserved_names.py", [])
    check("[CP7-T14] the writes and reserved-names rows hold both should-refuse and should-allow rows",
          {r.get("expect") for r in wr} >= {"refused", "allowed"}, sorted({r.get("expect") for r in wr}))
    probe = [r for r in rows if r.get("source") in ("director probe", "dogfood log")]
    for label, shape in list(WRITE_SHAPES.items()) + list(READ_SHAPES.items()):
        check(f"[CP7-T14] the '{label}' row is carried by the director probe or dogfood log, not only a test file",
              any(shape(r["command"]) for r in probe if isinstance(r.get("command"), str)), None)


def cp7_x01_extra_probe_rows_match_source():
    """[CP7-X01] A director-probe row whose command has a copy in test_guard_writes.py equals that literal exactly."""
    import ast
    src = (HERE / "test_guard_writes.py").read_text()
    lits = {n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    def key(c):
        return re.sub(r"\s+", " ", re.sub(r"\.ratchet/runs(/[\w-]+)?", "R", c)).strip()

    keys = {key(l) for l in lits}
    rows = [r for r in rows_of_table() if r.get("source") == "director probe" and isinstance(r.get("command"), str)]
    check("[CP7-X01] the table has director probe rows", bool(rows), None)
    copied = [r for r in rows if r["command"] in lits or key(r["command"]) in keys]
    check("[CP7-X01] at least one director probe row has a copy in test_guard_writes.py", bool(copied), None)
    off = [(r.get("id"), r["command"]) for r in copied if r["command"] not in lits]
    check("[CP7-X01] every such row equals its source literal exactly", off == [], off)


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
                   if callable(fn) and (m := re.match(r"(cp7_[tx]\d+)_", name)))
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
