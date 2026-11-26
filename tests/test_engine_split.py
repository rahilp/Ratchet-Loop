#!/usr/bin/env python3
"""Lane S invariants: tiny launcher, engine body, load rules."""
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
LAUNCHER = SCRIPTS / "ratchet.py"
ENGINE = SCRIPTS / "ratchet_engine.py"
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {detail!s}"[:400]))


def t01_files_exist():
    check("[S-T01] ratchet_engine.py exists", ENGINE.is_file(), ENGINE)
    check("[S-T01] ratchet.py exists", LAUNCHER.is_file(), LAUNCHER)


def t02_launcher_is_tiny():
    lines = LAUNCHER.read_text(encoding="utf-8").splitlines()
    check("[S-T02] launcher has at most 12 non-empty lines",
          sum(1 for l in lines if l.strip()) <= 12, len(lines))
    text = LAUNCHER.read_text(encoding="utf-8")
    check("[S-T02] launcher imports ratchet_engine.main",
          "from ratchet_engine import main" in text or "import ratchet_engine" in text, text)


def t03_engine_defines_main():
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    check("[S-T03] ratchet_engine.py defines main", "main" in names, sorted(names)[:20])


def t04_launcher_help():
    r = subprocess.run([sys.executable, str(LAUNCHER), "--help"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    check("[S-T04] launcher --help exits 0", r.returncode == 0, r.stderr[-400:])
    check("[S-T04] launcher --help mentions usage/subcommands",
          "usage" in (r.stdout + r.stderr).lower() or "init" in (r.stdout + r.stderr).lower(),
          (r.stdout + r.stderr)[:400])


def t05_inprocess_loads_engine_file():
    spec = importlib.util.spec_from_file_location("ratchet_engine_split_guard", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    check("[S-T05] loading ratchet_engine.py exposes main", callable(getattr(m, "main", None)))


def t06_no_internal_loader_points_at_launcher():
    bad = []
    for path in SCRIPTS.glob("*.py"):
        if path.name in ("ratchet.py", "ratchet_engine.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if 'with_name("ratchet.py")' in text or "with_name('ratchet.py')" in text:
            bad.append(path.name)
    check("[S-T06] sibling _ratchet_module paths do not load the launcher",
          not bad, bad)


def t07_emitted_paths_name_launcher():
    spec = importlib.util.spec_from_file_location("ratchet_engine_split_paths", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    check("[S-T07] ENGINE_PATH is the launcher",
          Path(m.ENGINE_PATH).resolve() == LAUNCHER.resolve(), m.ENGINE_PATH)
    check("[S-T07] LAUNCHER constant points at ratchet.py",
          Path(m.LAUNCHER).resolve() == LAUNCHER.resolve(), getattr(m, "LAUNCHER", None))
    hook = m._hook_cmd("claude", "guard")
    check("[S-T07] _hook_cmd embeds ratchet.py not ratchet_engine.py",
          "ratchet.py" in hook and "ratchet_engine.py" not in hook, hook)
    retry = m._retry_command()
    check("[S-T07] _retry_command names the launcher",
          "ratchet.py" in retry and "ratchet_engine.py" not in retry, retry)


CASES = [
    ("S-T01", t01_files_exist), ("S-T02", t02_launcher_is_tiny), ("S-T03", t03_engine_defines_main),
    ("S-T04", t04_launcher_help), ("S-T05", t05_inprocess_loads_engine_file),
    ("S-T06", t06_no_internal_loader_points_at_launcher),
    ("S-T07", t07_emitted_paths_name_launcher),
]


if __name__ == "__main__":
    for cid, fn in CASES:
        print(cid)
        try:
            fn()
        except Exception as e:
            check(f"[{cid}] raised", False, e)
    print(f"{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)