#!/usr/bin/env python3
"""
CP3 adversarial round 1, finding A1: the writer must not write what the loader refuses.
   python3 tests/test_ffl_provider_add.py

CP3 made the project's .ratchet/config.json refuse a wrongly shaped provider field at load (for_risk must be a list of the
risk names low, normal, high; command a non-empty list of non-empty strings). `providers --add` still wrote --for-risk and
--command items unchecked, so `providers --add y --no-check --preset gemini --for-risk High` said "It can now review", and
from then on every command (even `providers --remove y`) refused the file. These checks pin that a wrong --for-risk word or a
--command with a non-string item is refused in one plain line that names the flag and the valid shape, with a non-zero exit
code, no traceback, the settings file byte-identical, and the project still loading (`status` exits 0). Valid values still add.
There is no machine-wide variant: `providers` has no --global flag (only `config` does), so there is nothing to test there.

In-process through the engine's own entry points, with the helpers of tests/test_config_layers.py. No network, nothing spent.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402,F401
import test_config_layers as cl                         # noqa: E402
import test_agents_session as se                        # noqa: E402

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP3-[TX]\d+)\]")


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


run, text = cl.run, cl.text


def world():
    d, env = cl.project({})
    se.clean(env, "claude")
    return d, env


def cfg_bytes(d):
    p = cl.project_path(d)
    return p.read_bytes() if p.exists() else None


def lines_of(r):
    return [ln for ln in text(r).splitlines() if ln.strip()]


# (label, extra `providers --add` arguments, the flag the line must name, the shape word it must hold)
BAD_FOR_RISK = [
    ("High (wrong case)", ["--preset", "gemini", "--for-risk", "High"]),
    ("urgent (unknown risk)", ["--preset", "gemini", "--for-risk", "urgent"]),
    ("low,High (one good, one wrong)", ["--preset", "gemini", "--for-risk", "low,High"]),
    ("high,urgent (one good, one wrong)", ["--preset", "gemini", "--for-risk", "high,urgent"]),
]
BAD_COMMAND = [
    ("a number item", ["--command", '["mycli", 5]']),
    ("only a number", ["--command", "[5]"]),
    ("a null item", ["--command", '["mycli", null]']),
    ("a list item", ["--command", '["mycli", ["a"]]']),
    ("a boolean item", ["--command", '["mycli", true]']),
]


def refused(label, d, env, add_args, flag, shape):
    before = cfg_bytes(d)
    r = run(d, env, "providers", "--add", "y", "--no-check", "--model", "m", "--company", "acme", *add_args)
    out, lines = text(r), lines_of(r)
    check(f"[CP3-X10] {label}: refused with a non-zero exit code", r.returncode != 0, f"exit {r.returncode}: {out[:160]!r}")
    check(f"[CP3-X10] {label}: no traceback and no claim it was added",
          "Traceback" not in out and "It can now review" not in out and "Added provider" not in out, out[:200])
    check(f"[CP3-X10] {label}: exactly one plain line", len(lines) == 1, f"{len(lines)} lines: {out[:200]!r}")
    line = lines[0] if lines else ""
    check(f"[CP3-X10] {label}: the line names {flag} and the valid shape ({shape})", flag in line and shape in line, line)
    check(f"[CP3-X10] {label}: the settings file is byte-identical", cfg_bytes(d) == before, "file changed")
    st = run(d, env, "status")
    check(f"[CP3-X10] {label}: the project still loads (status exits 0)", st.returncode == 0, text(st)[-200:])
    rm = run(d, env, "providers", "--remove", "y")
    check(f"[CP3-X10] {label}: providers --remove is not blocked by a bad file", "must be a" not in text(rm), text(rm)[:200])


def t_bad_for_risk():
    for label, args in BAD_FOR_RISK:
        d, env = world()
        refused("--for-risk " + label, d, env, args, "--for-risk", "low, normal, high")


def t_bad_command():
    for label, args in BAD_COMMAND:
        d, env = world()
        refused("--command " + label, d, env, args, "--command", "string")


def t_valid_still_adds():
    d, env = world()
    r = run(d, env, "providers", "--add", "y", "--no-check", "--preset", "gemini", "--for-risk", "low,normal")
    check("[CP3-X10] valid --for-risk low,normal is added", r.returncode == 0 and "Added provider y" in text(r), text(r)[:200])
    cfg = json.loads(cfg_bytes(d))
    y = [p for p in cfg.get("providers", []) if p.get("id") == "y"]
    check("[CP3-X10] the entry holds exactly the risks given", len(y) == 1 and y[0].get("for_risk") == ["low", "normal"], y)
    st = run(d, env, "status")
    check("[CP3-X10] a valid add leaves the project loadable", st.returncode == 0, text(st)[-200:])

    d, env = world()
    r = run(d, env, "providers", "--add", "z", "--no-check", "--model", "m", "--company", "acme",
            "--command", '["mycli", "run", "{prompt}"]')
    check("[CP3-X10] a valid --command (all strings) is added", r.returncode == 0 and "Added provider z" in text(r), text(r)[:200])
    cfg = json.loads(cfg_bytes(d))
    z = [p for p in cfg.get("providers", []) if p.get("id") == "z"]
    check("[CP3-X10] the entry holds exactly the command given",
          len(z) == 1 and z[0].get("command") == ["mycli", "run", "{prompt}"], z)
    st = run(d, env, "status")
    check("[CP3-X10] a valid command add leaves the project loadable", st.returncode == 0, text(st)[-200:])


TESTS = [("CP3-X10", fn) for fn in (t_bad_for_risk, t_bad_command, t_valid_still_adds)]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": "CP3-X10", "status": "passed" if CASE_STATUS.get("CP3-X10", False) else "failed",
              "message": "" if CASE_STATUS.get("CP3-X10", False) else "; ".join(CASE_MESSAGES.get("CP3-X10", []))[:300]}]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def main():
    global FAIL
    CASE_STATUS.setdefault("CP3-X10", True)
    for _cid, fn in TESTS:
        try:
            fn()
        except Exception as e:
            FAIL += 1
            CASE_STATUS["CP3-X10"] = False
            CASE_MESSAGES.setdefault("CP3-X10", []).append(f"crashed: {e}")
            print(f"  ✗ [CP3-X10] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
