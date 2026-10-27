#!/usr/bin/env python3
"""
CP6 tests: run bookkeeping, hook-env and integrity checks (_set_blocked/ratchet_home/the hook-env path & record
helpers -- _hook_env_path/_same_path/_current_hook_env/_hook_role/_hook_env_records/_combined_hook_env --/
record_hook_env/hooks_installed_anywhere/hooks_in_use/hook_env_problem/require_hook_env/the active-run marker
helpers -- _markers_path/_restriction_record_damaged/_active_runs_repair/_active_marker --/register_active/the
reply classifiers -- choice_letter/message_intent/hooks_waive_intent/is_listed_yes --/the integrity helpers --
approval_snapshot/integrity_problems/verify_integrity --) move, verbatim, into
skills/ratchet-orchestrator/scripts/run_records.py.   python3 tests/test_modularize_cp6.py

AST diffs against a pinned pre-move source prove every moved body is byte-for-byte unchanged, that ratchet.py's
import of run_records is a plain, explicit `from run_records import ...` list (no star, no leftover `def`),
that run_records.py has no circular import with ratchet.py and introduces no new module-level mutable
registry. The byte-identical CLI/status/hook/exit-code comparison re-runs CP1's saved baseline
(tests/fixtures/modularize_cp1/baseline/) the way CP2-T11/CP3-T11/CP4-05..08/CP5-T05 already did; the
hook-env-specific behaviors CP1 never captured (record_hook_env's round trip, doctor's hook fields, the
"hook record missing" and "hook record looks stale" refusals, the busy two-runs-same-project message, the
reply-classifier battery, and the integrity/approve-changes refusal, including its director-enrolled wording)
instead use the same pinned-vs-current-on-one-fixture technique CP3-T07/CP4-06/CP5-T04 already established:
the SAME pinned pre-move source is written into skills/ratchet-orchestrator/scripts/ (so its own sibling
imports -- engine_utils/fingerprint/records/provider_discovery -- resolve exactly like ratchet.py's own
`sys.path.insert(0, str(SCRIPT_DIR))` does), run or loaded alongside the current, already-split engine, on an
identical fixture, and compared byte-for-byte.

Pin note: unlike CP2..CP5, this checkpoint canNOT reuse CP1's own pinned commit (d0f7bd147f2990ace56a46a189b4e9
200e345d99) as its "pre-move" baseline for ALL 25 names -- confirmed by hand (git show + AST diff) that
approval_snapshot's own body legitimately changed after that commit (an unrelated main-merge fix, "approve-
changes stamps the prior pin, not resolved reviewers", commit 11e8eaf, landed between CP1 and CP2 while this
run's own checkpoints moved OTHER code), so d0f7bd1's copy of approval_snapshot is stale text, not CP6's real
pre-move body. Since CP6 has not moved anything yet, "the commit right before CP6's move lands" is simply the
current HEAD (65001a8689e267b858a4fbc242f409412cdc4e61, CP5's own landing commit) -- confirmed by hand that
`git status` shows no uncommitted change to ratchet.py and that all 25 names are still top-level defs there,
byte-for-byte what `git show HEAD:...` returns.

CP6-T17/T18/T19 (the regression-file and earlier-checkpoint-behavior cases) are kept to structural/targeted
checks only, per .ratchet/learnings.md's director steer: this checkpoint's own tests.command already runs all
ten named regression files for real, in the same run_all.py invocation as this file, and the heavy pre-existing
suites (test_trust_cp5_d.py/_e.py, test_quota_shared.py) are run once, project-wide, by `ratchet.py complete`;
nesting another full run of any of them here would double real time for no extra coverage.

Just a plain check()/PASS/FAIL tally (this checkpoint's tests.report is "none": exit-code-only checking, the
same ad hoc tests/run_all.py design every checkpoint in this run uses) -- no RATCHET_TEST_REPORT JSON writer.
"""
from __future__ import annotations

import ast
import contextlib
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import test_gates as g  # noqa: E402  (project, approve_plan, rx, say, ENV)
import trust_helpers  # noqa: E402
from trust_helpers import ENGINE, cp5y_load_engine, engine_env, make_env  # noqa: E402

SCRIPT_DIR = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
RUN_RECORDS = SCRIPT_DIR / "run_records.py"
sys.path.insert(0, str(SCRIPT_DIR))  # [CP6-X02] lets this file import run_records directly, the same way an
                                      # engine instance's own top-level code does for its own sibling imports
import run_records  # noqa: E402  (CP6-X02's own module object: reaches run_records._HOST/_current_hook_env directly)
# See the module docstring's "Pin note": the tree right now (this commit) is CP6's own "pre-move" state --
# confirmed by hand that none of the 25 names below differs between this commit and the live working tree.
PRE_MOVE_COMMIT = "65001a8689e267b858a4fbc242f409412cdc4e61"
PRE_MOVE_RELPATH = "skills/ratchet-orchestrator/scripts/ratchet.py"

FUNCS = [
    "_set_blocked", "ratchet_home", "_hook_env_path", "_same_path", "_current_hook_env", "_hook_role",
    "_hook_env_records", "_combined_hook_env", "record_hook_env", "hooks_installed_anywhere", "hooks_in_use",
    "hook_env_problem", "require_hook_env", "_markers_path", "_restriction_record_damaged",
    "_active_runs_repair", "_active_marker", "register_active", "choice_letter", "message_intent",
    "hooks_waive_intent", "is_listed_yes", "approval_snapshot", "integrity_problems", "verify_integrity",
]
ALL_NAMES = list(FUNCS)  # this checkpoint moves no classes

BASELINE_DIR = ROOT / "tests" / "fixtures" / "modularize_cp1" / "baseline"
MANIFEST = BASELINE_DIR / "manifest.json"

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ---- git / pinned-source plumbing -----------------------------------------------------------------------


def show_at(commit: str, relpath: str) -> str:
    r = subprocess.run(["git", "show", f"{commit}:{relpath}"], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"git show failed: {r.returncode} {r.stderr[-300:]}")
    return r.stdout


def pinned_ratchet_source() -> str:
    return show_at(PRE_MOVE_COMMIT, PRE_MOVE_RELPATH)


def live_ratchet_source() -> str:
    return ENGINE.read_text()


def top_level_defs(tree) -> dict:
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def assign_value_dump(tree, name):
    """ast.dump() of the value a top-level `name = ...` Assign holds, or None when there is no such Assign."""
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.dump(n.value)
    return None


def _read_manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError):
        return None


_PINNED_SCRIPT_PATH: "Path | None" = None


def _write_pinned_script() -> Path:
    """Returns the ONE pinned pre-move source file shared by the whole suite run, writing it into the real
    scripts folder (not some unrelated tmp dir) exactly once, the first time any case calls this (normally
    main(), near the top, before the TESTS loop runs). Its unique name still lets its own
    `SCRIPT_DIR = Path(__file__).resolve().parent` and `sys.path.insert(0, str(SCRIPT_DIR))` resolve to the real
    scripts folder and its sibling imports (engine_utils/fingerprint/records/provider_discovery) work exactly
    like ratchet.py's own do -- the same technique CP4-06/CP5-T04 already established. Every later call
    (including from _pinned_module() below) reuses this SAME path and never rewrites or deletes it; only
    main()'s own `finally` deletes it, once, at the very end of the whole run.

    The filename ends in `.py.tmp`, not `.py`: ratchet.py's own `_new_source()` (the function backing
    `_Watch.changed()`) classifies any new file as a real code change unless its name matches one of
    `_WATCH_GENERATED_NAMES`'s own scratch-file patterns, one of which is the exact glob `*.tmp`. A plain
    `*_cp6_pinned_*.py` name matches none of those patterns, so a concurrent `ratchet.py behavior CP6`
    tree-watch's periodic poll (or its own final look) would deterministically flag it as new code the moment
    it lands on disk -- this is not a race to avoid by keeping the exposure window short, it is a guaranteed
    false positive for as long as the file exists under that name. The `.py.tmp` suffix makes `_new_source()`
    return False for it unconditionally, for however long it lives, so the shared-script lifetime below is no
    longer load-bearing for correctness; it is kept anyway because it also avoids repeated git-show calls."""
    global _PINNED_SCRIPT_PATH
    if _PINNED_SCRIPT_PATH is None or not _PINNED_SCRIPT_PATH.exists():
        src = pinned_ratchet_source()
        p = SCRIPT_DIR / f"_cp6_pinned_{os.getpid()}_{secrets.token_hex(4)}.py.tmp"
        p.write_text(src)
        _PINNED_SCRIPT_PATH = p
    return _PINNED_SCRIPT_PATH


def _pinned_module():
    """A fresh module object for the pinned pre-move source, loaded via spec_from_file_location the same way
    cp5y_load_engine() loads the current engine -- reuses the single shared pinned-script path (written once,
    by main(), and deleted once, by main()'s own `finally`); this function does NOT delete it itself, since
    other cases may still need the file after this call returns. The pinned file's real name ends in `.py.tmp`
    (see _write_pinned_script()'s docstring for why), which has no extension Python's import machinery
    recognizes, so spec_from_file_location is given an explicit SourceFileLoader rather than being left to
    infer one from the (unrecognized) extension -- it otherwise returns a spec with no usable loader."""
    p = _write_pinned_script()
    name = f"ratchet_cp6_pinned_{os.getpid()}_{secrets.token_hex(4)}"
    loader = importlib.machinery.SourceFileLoader(name, str(p))
    spec = importlib.util.spec_from_file_location(name, p, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plant_crash_marker(root: Path, host="claude", event="guard", at=None) -> "dt.datetime":
    """Plants an unresolved hook-crash marker directly (the same on-disk shape run_hook writes in
    .ratchet/hook-crashes/), with no real hook failure needed, so hook_env_problem's "a hook may have failed to
    save it" / "may be out of date" branches can be driven on demand."""
    at = at or dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    us = int((at - epoch).total_seconds() * 1_000_000)
    folder = root / ".ratchet" / "hook-crashes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{host}.{event}.crash.{us}.cp6fix").write_text("")
    return at


# ============================================================================================ CP6-T01


def t01_module_byte_identical_and_explicit_imports():
    print("CP6-T01: run_records.py's 25 moved definitions are byte-identical to before the move, and ratchet.py "
          "imports each one by its own explicit name")
    try:
        pinned_src = pinned_ratchet_source()
    except Exception as e:
        check("[CP6-T01] setup: the pinned pre-move ratchet.py source is readable via git show", False, str(e))
        return
    pinned_tree = ast.parse(pinned_src, filename="ratchet.py@pre-move")
    pinned_defs = top_level_defs(pinned_tree)
    missing_pinned = [n for n in ALL_NAMES if n not in pinned_defs]
    check("[CP6-T01] setup: all 25 names are real top-level defs in the pinned pre-move source",
          not missing_pinned, missing_pinned)
    if missing_pinned:
        return

    check("[CP6-T01] run_records.py exists", RUN_RECORDS.exists())
    if not RUN_RECORDS.exists():
        return
    rr_src = RUN_RECORDS.read_text()
    try:
        rr_tree = ast.parse(rr_src, filename="run_records.py")
    except SyntaxError as e:
        check("[CP6-T01] run_records.py parses as valid Python", False, str(e))
        return
    rr_defs = top_level_defs(rr_tree)

    for name in ALL_NAMES:
        check(f"[CP6-T01] {name} is defined at top level in run_records.py", name in rr_defs)
        if name not in rr_defs:
            continue
        pinned_seg = ast.get_source_segment(pinned_src, pinned_defs[name])
        rr_seg = ast.get_source_segment(rr_src, rr_defs[name])
        check(f"[CP6-T01] {name}'s body in run_records.py is byte-for-byte identical to its pre-move body",
              rr_seg == pinned_seg, "" if rr_seg == pinned_seg else (pinned_seg, rr_seg))

    live_src = live_ratchet_source()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
        live_defs = top_level_defs(live_tree)
    except SyntaxError as e:
        check("[CP6-T01] the current ratchet.py parses as valid Python", False, str(e))
        return
    leftover = [n for n in ALL_NAMES if n in live_defs]
    check("[CP6-T01] none of the 25 names has its own `def` left behind in ratchet.py's top-level AST",
          not leftover, leftover)

    star_live = [n for n in ast.walk(live_tree)
                 if isinstance(n, ast.ImportFrom) and n.module == "run_records"
                 and any(a.name == "*" for a in n.names)]
    check("[CP6-T01] ratchet.py never does `from run_records import *`", not star_live)
    named_imports = set()
    for n in ast.walk(live_tree):
        if isinstance(n, ast.ImportFrom) and n.module == "run_records":
            named_imports.update(a.name for a in n.names if a.name != "*")
    still_missing = [n for n in ALL_NAMES if n not in named_imports]
    check("[CP6-T01] ratchet.py imports each of the 25 names explicitly by name from run_records "
          "(`from run_records import ...`)", not still_missing, sorted(named_imports))


# ============================================================================================ CP6-T02..T05
# The same byte-identical CLI/status/hook/exit-code comparison CP2-T11/CP3-T11/CP4-05..08/CP5-T05 already run
# against CP1's saved baseline, split into the 4 cases the plan names.


def t02_help_byte_identical():
    print("CP6-T02: --help (top-level and a few subcommands) is byte-identical to CP1's baseline")
    manifest = _read_manifest()
    check("[CP6-T02] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
          "missing or invalid" if manifest is None else "")
    if manifest is None:
        return
    for e in manifest.get("entries") or []:
        if e.get("kind") != "help":
            continue
        cmd = e.get("cmd") or []
        try:
            saved = (BASELINE_DIR / e["path"]).read_text()
        except OSError as ex:
            check(f"[CP6-T02] baseline file for `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        r = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1",
                                                 PYTHON_COLORS="0"))
        check(f"[CP6-T02] `ratchet.py {' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              r.returncode == e.get("exit_code"), r.returncode)
        check(f"[CP6-T02] `ratchet.py {' '.join(cmd)}`'s stdout is byte-identical to CP1's baseline",
              r.stdout == saved, (saved[:200], r.stdout[:200]))


def t03_status_json_byte_identical():
    print("CP6-T03: status --json is byte-identical to CP1's baseline sample")
    manifest = _read_manifest()
    check("[CP6-T03] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
          "missing or invalid" if manifest is None else "")
    if manifest is None:
        return
    samples = [e for e in manifest.get("entries") or [] if e.get("kind") == "status_json"]
    check("[CP6-T03] setup: a status_json baseline entry is recorded", bool(samples), manifest.get("entries"))
    if not samples:
        return
    try:
        saved_status = json.loads((BASELINE_DIR / samples[0]["path"]).read_text())
    except (OSError, json.JSONDecodeError) as ex:
        check("[CP6-T03] the saved status --json baseline parses as JSON", False, str(ex))
        return
    r = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=30)
    try:
        live_status = json.loads(r.stdout) if r.returncode == 0 else None
    except json.JSONDecodeError:
        live_status = None
    check("[CP6-T03] a live `status --json` invocation right now succeeds", live_status is not None,
          (r.returncode, r.stdout[-300:]))
    if live_status is not None:
        check("[CP6-T03] status --json's top-level key set is unchanged from CP1's baseline",
              set(live_status) == set(saved_status), (sorted(live_status), sorted(saved_status)))

    # Full-output comparison on a reproducible fixture, the same pinned-vs-current-on-one-fixture technique
    # CP4-06 already established (CP1's own saved sample is captured from this run's own ever-advancing state,
    # so a full-content comparison against it can never hold past CP1 itself).
    pinned_script = _write_pinned_script()
    try:
        d = Path(_tmp.mkdtemp(prefix="cp6-t03-status-"))
        r_init = subprocess.run([sys.executable, str(ENGINE), "init", "status parity cap", "--slug", "st"],
                                cwd=str(d), capture_output=True, text=True, timeout=30)
        check("[CP6-T03] setup: a fresh throwaway project's `init` succeeds", r_init.returncode == 0,
              (r_init.returncode, r_init.stderr))
        if r_init.returncode != 0:
            return
        r_before = subprocess.run([sys.executable, str(pinned_script), "status", "--json"], cwd=str(d),
                                  capture_output=True, text=True, timeout=30)
        r_after = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(d),
                                 capture_output=True, text=True, timeout=30)
        check("[CP6-T03] setup: the pinned pre-move engine's `status --json` exits 0 on the fresh fixture",
              r_before.returncode == 0, (r_before.returncode, r_before.stdout[-300:], r_before.stderr[-300:]))
        check("[CP6-T03] setup: the current ratchet.py's `status --json` exits 0 on the same fresh fixture",
              r_after.returncode == 0, (r_after.returncode, r_after.stdout[-300:], r_after.stderr[-300:]))
        if r_before.returncode == 0 and r_after.returncode == 0:
            check("[CP6-T03] the complete status --json output (not just the top-level key set checked above) "
                  "is byte-identical between the pinned pre-move engine and the current, already-split engine, "
                  "on the same fresh fixture", r_before.stdout == r_after.stdout, (r_before.stdout, r_after.stdout))
    finally:
        pass  # the shared pinned script is deleted once, by main()'s own `finally`, not per call site


def t04_hook_reply_byte_identical():
    print("CP6-T04: a sample prompt/stop hook reply is byte-identical to CP1's baseline")
    d = Path(_tmp.mkdtemp(prefix="cp6-t04-hook-"))
    r = subprocess.run([sys.executable, str(ENGINE), "init", "hook cap", "--slug", "hk"], cwd=str(d),
                       capture_output=True, text=True, timeout=30)
    check("[CP6-T04] setup: a fresh throwaway project's `init` succeeds", r.returncode == 0, (r.returncode, r.stderr))
    if r.returncode != 0:
        return
    state = str(d / ".ratchet" / "runs" / "hk" / "state.json")
    payload = {"sessionId": "s", "timestamp": 1, "cwd": str(d), "toolName": "edit",
               "toolArgs": json.dumps({"path": state, "old_str": "a", "new_str": "b"})}
    rg = subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "copilot"], cwd=str(d),
                        capture_output=True, text=True, timeout=30, input=json.dumps(payload))
    try:
        reply = json.loads(rg.stdout)
    except json.JSONDecodeError as e:
        check("[CP6-T04] a fresh copilot guard reply parses as JSON", False, str(e))
        return
    manifest = _read_manifest()
    check("[CP6-T04] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
          "missing or invalid" if manifest is None else "")
    if manifest is None:
        return
    hook_samples = [e for e in manifest.get("entries") or [] if e.get("kind") == "hook_reply"]
    check("[CP6-T04] setup: a hook_reply baseline entry is recorded", bool(hook_samples), manifest.get("entries"))
    if not hook_samples:
        return
    try:
        saved_reply = json.loads((BASELINE_DIR / hook_samples[0]["path"]).read_text())
    except (OSError, json.JSONDecodeError) as ex:
        check("[CP6-T04] the saved hook reply baseline parses as JSON", False, str(ex))
        return

    def _normalize_blocked_path(reply_):
        reason_ = reply_.get("permissionDecisionReason", "")
        return {**reply_, "permissionDecisionReason": re.sub(r"Blocked: .*$", "Blocked: <path>", reason_)}

    check("[CP6-T04] the complete fresh guard reply matches CP1's saved baseline exactly, once each one's own "
          "random temp-dir path is normalized away",
          _normalize_blocked_path(reply) == _normalize_blocked_path(saved_reply), (saved_reply, reply))


def t05_failure_exit_codes_unchanged():
    print("CP6-T05: at least two known failure exit codes are unchanged")
    manifest = _read_manifest()
    check("[CP6-T05] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
          "missing or invalid" if manifest is None else "")
    if manifest is None:
        return
    failures = [e for e in manifest.get("entries") or [] if e.get("kind") == "failure"]
    check("[CP6-T05] setup: at least two failure baseline entries are recorded", len(failures) >= 2, failures)
    for e in failures:
        cmd = e.get("cmd") or []
        try:
            saved = (BASELINE_DIR / e["path"]).read_text()
        except OSError as ex:
            check(f"[CP6-T05] baseline file for failure `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        if cmd and cmd[0] == "no-such-command":
            rf = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True,
                                text=True, timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1",
                                                                 PYTHON_COLORS="0"))
            got = rf.stderr
        else:  # "status --json (run from a directory with no .ratchet/ at all)"
            empty_dir = Path(_tmp.mkdtemp(prefix="cp6-t05-empty-"))
            rf = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(empty_dir),
                                capture_output=True, text=True, timeout=30)
            got = rf.stderr
        check(f"[CP6-T05] failure case `{' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              rf.returncode == e.get("exit_code"), rf.returncode)
        check(f"[CP6-T05] failure case `{' '.join(cmd)}`'s stderr is byte-identical to CP1's baseline",
              got == saved, (saved, got))


# ============================================================================================ CP6-T06


def t06_hook_env_round_trip_unchanged():
    print("CP6-T06: hook-env.json read/write round-trips identically before and after the move")
    pinned = _pinned_module()
    current = cp5y_load_engine()
    env = make_env("claude")
    started = "2026-01-15T10:00:00.000000+00:00"
    results = {}
    for label, mod in (("current", current), ("pinned", pinned)):
        root = Path(_tmp.mkdtemp(prefix=f"cp6-t06-{label}-"))
        (root / ".ratchet").mkdir()
        with engine_env(env):
            ok = mod.record_hook_env(root, "claude", event="guard", started=started)
            rec = json.loads((root / ".ratchet" / "hook-env.json").read_text()) if ok else None
            problem = mod.hook_env_problem(root)
        results[label] = (ok, rec, problem)
    ok_cur, rec_cur, prob_cur = results["current"]
    ok_pin, rec_pin, prob_pin = results["pinned"]
    check("[CP6-T06] setup: both the current and pinned engine's record_hook_env succeed",
          ok_cur and ok_pin, (ok_cur, ok_pin))
    if not (ok_cur and ok_pin):
        return

    def _norm(rec):  # `at` (now()) is the one genuinely variable field
        return {k: v for k, v in rec.items() if k != "at"}

    check("[CP6-T06] the written hook-env.json record (home, key_file, guard_role) is identical before and "
          "after the move", _norm(rec_cur) == _norm(rec_pin), (rec_cur, rec_pin))
    check("[CP6-T06] hook_env_problem's verdict on that same record is identical (None: it agrees with the "
          "current per-user folder)", prob_cur is None and prob_pin is None, (prob_cur, prob_pin))


# ============================================================================================ CP6-T07


def t07_doctor_hooks_fields_unchanged():
    print("CP6-T07: doctor --json's hooks.* fields are unchanged for a fresh and a stale heartbeat")
    env = make_env("claude")
    d_fresh = trust_helpers.project(env)
    d_stale = Path(_tmp.mkdtemp(prefix="cp6-t07-stale-"))
    shutil.copytree(d_fresh, d_stale, dirs_exist_ok=True)
    hb_path = trust_helpers.hb_path(d_stale)
    try:
        hb = json.loads(hb_path.read_text())
    except (OSError, ValueError) as e:
        check("[CP6-T07] setup: the fresh fixture's hook-heartbeat.json is readable", False, str(e))
        return
    old = trust_helpers.iso_ago(25 * 3600)
    for ev in list(hb):
        if isinstance(hb[ev], dict):
            hb[ev]["at"] = old
    hb_path.write_text(json.dumps(hb))

    pinned_script = _write_pinned_script()
    try:
        for label, d in (("fresh", d_fresh), ("stale", d_stale)):
            r_cur = subprocess.run([sys.executable, str(ENGINE), "doctor", "--json"], cwd=str(d),
                                   capture_output=True, text=True, env=env, timeout=30)
            r_pin = subprocess.run([sys.executable, str(pinned_script), "doctor", "--json"], cwd=str(d),
                                   capture_output=True, text=True, env=env, timeout=30)
            check(f"[CP6-T07] setup: both current and pinned doctor --json succeed on the {label} fixture",
                  r_cur.returncode == 0 and r_pin.returncode == 0,
                  (r_cur.returncode, r_pin.returncode, r_cur.stderr[-200:], r_pin.stderr[-200:]))
            try:
                cur_j = json.loads(r_cur.stdout).get("hooks", {})
                pin_j = json.loads(r_pin.stdout).get("hooks", {})
            except json.JSONDecodeError as e:
                check(f"[CP6-T07] {label}: both doctor --json outputs parse", False, str(e))
                continue
            for f in ("installed", "heartbeat_seen", "fresh", "enforcing", "prompt_hook"):
                check(f"[CP6-T07] doctor --json hooks.{f} ({label} fixture) is unchanged before/after the move",
                      cur_j.get(f) == pin_j.get(f), (f, pin_j.get(f), cur_j.get(f)))
            if label == "fresh":
                check("[CP6-T07] the fresh fixture really shows fresh, enforcing hooks (not a vacuous pass)",
                      cur_j.get("fresh") is True and cur_j.get("enforcing") is True, cur_j)
            else:
                check("[CP6-T07] the stale fixture really shows NOT fresh (not a vacuous pass)",
                      cur_j.get("fresh") is False, cur_j)
    finally:
        pass  # the shared pinned script is deleted once, by main()'s own `finally`, not per call site


# ============================================================================================ CP6-T08


def t08_hook_missing_record_refusal_unchanged():
    print("CP6-T08: the 'hook record missing, a hook may have failed to save it' refusal text and exit code are "
          "unchanged")
    env = make_env("claude")
    pinned_script = _write_pinned_script()
    try:
        d = Path(_tmp.mkdtemp(prefix="cp6-t08-"))
        (d / ".ratchet").mkdir()
        _plant_crash_marker(d)  # unresolved crash: hooks_in_use() is True; hook-env.json is never written
        check("[CP6-T08] setup: the fixture really has no hook-env.json yet (the record-missing scenario)",
              not (d / ".ratchet" / "hook-env.json").exists())
        r_cur = subprocess.run([sys.executable, str(ENGINE), "plan-summary"], cwd=str(d), capture_output=True,
                               text=True, env=env, timeout=30)
        r_pin = subprocess.run([sys.executable, str(pinned_script), "plan-summary"], cwd=str(d),
                               capture_output=True, text=True, env=env, timeout=30)
        check("[CP6-T08] the refusal exits the same code (1) before and after the move",
              r_cur.returncode == 1 and r_pin.returncode == 1, (r_cur.returncode, r_pin.returncode))
        check("[CP6-T08] the refusal text is byte-identical before and after the move",
              r_cur.stderr == r_pin.stderr, (r_pin.stderr, r_cur.stderr))
        check("[CP6-T08] it really is the 'hook record missing, a hook may have failed to save it' refusal "
              "(not a different, unrelated failure)",
              "a hook may have failed to save it" in r_cur.stderr and "install-hooks" in r_cur.stderr
              and "doctor" in r_cur.stderr, r_cur.stderr)
    finally:
        pass  # the shared pinned script is deleted once, by main()'s own `finally`, not per call site


# ============================================================================================ CP6-T09


def t09_hook_stale_record_refusal_unchanged():
    print("CP6-T09: the 'hook record looks stale' refusal text and exit code are unchanged")
    env = make_env("claude")
    pinned_script = _write_pinned_script()
    try:
        d = Path(_tmp.mkdtemp(prefix="cp6-t09-"))
        (d / ".ratchet").mkdir()
        current = cp5y_load_engine()
        with engine_env(env):
            ok_write = current.record_hook_env(d, "claude", event="guard")
        check("[CP6-T09] setup: a real, matching hook-env.json record is written first",
              ok_write and (d / ".ratchet" / "hook-env.json").exists(), ok_write)
        if not ok_write:
            return
        _plant_crash_marker(d)  # an unresolved crash now doubts even the EXISTING, matching record
        r_cur = subprocess.run([sys.executable, str(ENGINE), "plan-summary"], cwd=str(d), capture_output=True,
                               text=True, env=env, timeout=30)
        r_pin = subprocess.run([sys.executable, str(pinned_script), "plan-summary"], cwd=str(d),
                               capture_output=True, text=True, env=env, timeout=30)
        check("[CP6-T09] the refusal exits the same code (1) before and after the move",
              r_cur.returncode == 1 and r_pin.returncode == 1, (r_cur.returncode, r_pin.returncode))
        check("[CP6-T09] the refusal text is byte-identical before and after the move",
              r_cur.stderr == r_pin.stderr, (r_pin.stderr, r_cur.stderr))
        check("[CP6-T09] it really is the 'hook record looks stale' refusal, naming doctor and accept-state",
              "may be out of date" in r_cur.stderr and "accept-state" in r_cur.stderr
              and "doctor" in r_cur.stderr, r_cur.stderr)
    finally:
        pass  # the shared pinned script is deleted once, by main()'s own `finally`, not per call site


# ============================================================================================ CP6-T10


def t10_two_runs_same_project_busy_message_unchanged():
    print("CP6-T10: 'two runs claim the same project' (register_active's busy-lock message) is still detected "
          "and reported the same way")
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    root = Path(_tmp.mkdtemp(prefix="cp6-t10-"))
    current = cp5y_load_engine()
    pinned = _pinned_module()
    results = {}
    for label, mod in (("current", current), ("pinned", pinned)):
        with engine_env(env):
            p = mod._markers_path()
            if p.exists():
                p.unlink()
            lock_path = mod._record_lock_path(p)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(f"{os.getpid() + 1} another-process")  # fresh: a live holder, never stale
            before = p.read_text() if p.exists() else None
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                mod.register_active(root, "second-run")
            after = p.read_text() if p.exists() else None
            lock_path.unlink(missing_ok=True)
        results[label] = (before, after, buf.getvalue())
    b_cur, a_cur, msg_cur = results["current"]
    b_pin, a_pin, msg_pin = results["pinned"]
    check("[CP6-T10] active-runs.json is untouched by the busy call (no entry silently written) both before and "
          "after the move", a_cur == b_cur and a_pin == b_pin, (b_cur, a_cur, b_pin, a_pin))
    check("[CP6-T10] the busy-lock stderr message is byte-identical before and after the move",
          msg_cur == msg_pin, (msg_pin, msg_cur))
    check("[CP6-T10] it really is the busy-lock message naming the lock and the repair steps (not silence)",
          "was busy" in msg_cur and "pause" in msg_cur and "resume" in msg_cur, msg_cur)


# ============================================================================================ CP6-T11


def t11_reply_classifiers_battery_unchanged():
    print("CP6-T11: the reply classifiers return identical verdicts on a fixed battery of sample replies")
    current = cp5y_load_engine()
    pinned = _pinned_module()
    hooks_waive_exact = "yes, go ahead without hooks"
    battery = [
        "yes",                      # a clear yes
        "nope, not now",            # a clear no
        "yes, but wait",            # a hedge word mixed with "yes"
        "ok",                       # a bare "ok" (not a yes)
        hooks_waive_exact,          # the exact hooks-waiver phrase
        "go ahead without the hooks",  # a near-miss waiver phrase
        "\U0001F44D",               # an emoji thumbs-up
        "好的",             # a non-ASCII/CJK reply
    ]
    options = ["A", "B"]
    for text in battery:
        got_cur = current.choice_letter(text, options)
        got_pin = pinned.choice_letter(text, options)
        check(f"[CP6-T11] choice_letter({text!r}, {options}) is identical before and after the move",
              got_cur == got_pin, (text, got_pin, got_cur))
        for fn_name in ("message_intent", "hooks_waive_intent", "is_listed_yes"):
            got_cur = getattr(current, fn_name)(text)
            got_pin = getattr(pinned, fn_name)(text)
            check(f"[CP6-T11] {fn_name}({text!r}) is identical before and after the move",
                  got_cur == got_pin, (text, fn_name, got_pin, got_cur))

    # known, named verdicts (pins actual behavior, not just pinned==current parity)
    check("[CP6-T11] a clear 'yes' is a listed approval", current.is_listed_yes("yes") is True)
    check("[CP6-T11] a clear 'yes' reads as an approval intent", current.message_intent("yes") == "approve")
    check("[CP6-T11] a hedge word mixed with 'yes' is never an approval intent",
          current.message_intent("yes, but wait") == "unclear")
    check("[CP6-T11] a bare 'ok' is never a listed yes", current.is_listed_yes("ok") is False)
    check("[CP6-T11] a bare 'ok' is never an approval intent", current.message_intent("ok") == "unclear")
    check("[CP6-T11] the exact hooks-waiver phrase approves waiving hooks",
          current.hooks_waive_intent(hooks_waive_exact) == "approve")
    check("[CP6-T11] a near-miss waiver phrase ('the hooks' instead of 'hooks') is never read as waiving",
          current.hooks_waive_intent("go ahead without the hooks") == "unclear")
    check("[CP6-T11] a non-ASCII/CJK reply is never a listed yes",
          current.is_listed_yes("好的") is False)
    check("[CP6-T11] a non-ASCII/CJK reply is never an approval intent",
          current.message_intent("好的") == "unclear")
    check("[CP6-T11] a direct letter choice is read cleanly", current.choice_letter("B", options) == "B")
    check("[CP6-T11] a letter choice with extra text attached is not a clear choice",
          current.choice_letter("B, but keep the old colour", options) is None)


# ============================================================================================ CP6-T12


def t12_integrity_check_and_approve_changes_unchanged():
    print("CP6-T12: integrity checks still catch a post-approval change (plain and director-enrolled wording), "
          "and approve-changes still clears it, unchanged")
    pinned_script = _write_pinned_script()
    try:
        d_cur = g.project("cp6t12cur")
        g.approve_plan(d_cur)
        d_pin = Path(_tmp.mkdtemp(prefix="cp6-t12-pinned-"))
        shutil.copytree(d_cur, d_pin, dirs_exist_ok=True)

        def _tamper(d):
            cpp = d / ".ratchet" / "runs" / "f" / "checkpoints.json"
            cpj = json.loads(cpp.read_text())
            cpj["checkpoints"][0]["summary"] = "a changed summary, after approval"
            cpp.write_text(json.dumps(cpj))

        _tamper(d_cur)
        _tamper(d_pin)
        # `tests-written CP1` runs _cur (phase/current checks) then verify_integrity before anything else, so
        # the post-approval edit above is what makes it die here -- plan-summary never calls verify_integrity
        # at all, so it would wrongly exit 0 on this same tampered fixture.
        r_cur = subprocess.run([sys.executable, str(ENGINE), "tests-written", "CP1"], cwd=str(d_cur),
                               capture_output=True, text=True, env=g.ENV, timeout=30)
        r_pin = subprocess.run([sys.executable, str(pinned_script), "tests-written", "CP1"], cwd=str(d_pin),
                               capture_output=True, text=True, env=g.ENV, timeout=30)

        def _norm(s):
            return s.replace(str(d_cur), "<root>").replace(str(d_pin), "<root>")

        check("[CP6-T12] setup: both runs' tests-written dies after the post-approval checkpoint edit",
              r_cur.returncode == 1 and r_pin.returncode == 1, (r_cur.returncode, r_pin.returncode))
        check("[CP6-T12] the integrity refusal text is byte-identical before and after the move",
              _norm(r_cur.stderr) == _norm(r_pin.stderr), (_norm(r_pin.stderr), _norm(r_cur.stderr)))
        check("[CP6-T12] it really names the checkpoint and checkpoints.json, exactly as before",
              "These changed after the user approved the plan" in r_cur.stderr
              and "checkpoint CP1 in checkpoints.json" in r_cur.stderr, r_cur.stderr)

        # the director-enrolled variant, forced via a damaged director.json (approval_mode's own "treat as
        # enrolled, even damaged" rule), on a separate pair of copies so the plain-variant fixtures above stay
        # untouched for the approve-changes check below.
        d_cur2 = Path(_tmp.mkdtemp(prefix="cp6-t12-dir-cur-"))
        d_pin2 = Path(_tmp.mkdtemp(prefix="cp6-t12-dir-pin-"))
        shutil.copytree(d_cur, d_cur2, dirs_exist_ok=True)
        shutil.copytree(d_cur, d_pin2, dirs_exist_ok=True)
        for d in (d_cur2, d_pin2):
            (d / ".ratchet" / "director.json").write_text("not json")
        r_cur2 = subprocess.run([sys.executable, str(ENGINE), "tests-written", "CP1"], cwd=str(d_cur2),
                                capture_output=True, text=True, env=g.ENV, timeout=30)
        r_pin2 = subprocess.run([sys.executable, str(pinned_script), "tests-written", "CP1"], cwd=str(d_pin2),
                                capture_output=True, text=True, env=g.ENV, timeout=30)

        def _norm2(s):
            return s.replace(str(d_cur2), "<root>").replace(str(d_pin2), "<root>")

        check("[CP6-T12] the director-enrolled integrity refusal text is byte-identical before and after the "
              "move", _norm2(r_cur2.stderr) == _norm2(r_pin2.stderr), (_norm2(r_pin2.stderr), _norm2(r_cur2.stderr)))
        check("[CP6-T12] it really is the director-enrolled variant (names director-enrolled/approve-changes "
              "for the lane)", "director-enrolled" in r_cur2.stderr and "approve-changes" in r_cur2.stderr,
              r_cur2.stderr)

        # approve-changes clears the plain-variant integrity problem exactly as before: a later tests-written no
        # longer dies for this reason (current engine only -- the refusal itself is already proven identical
        # before/after the move above).
        g.rx(d_cur, "ask", "approve-changes")
        g.say(d_cur, "yes, go ahead")
        r_ac = g.rx(d_cur, "approve-changes")
        check("[CP6-T12] approve-changes clears the integrity problem", r_ac.returncode == 0,
              (r_ac.returncode, r_ac.stdout, r_ac.stderr))
        r_after = g.rx(d_cur, "tests-written", "CP1")
        check("[CP6-T12] ... and a later tests-written no longer dies for the integrity reason (it may still "
              "refuse for an unrelated reason, e.g. the wrong step)",
              "These changed after the user approved the plan" not in r_after.stderr, r_after.stderr)
    finally:
        pass  # the shared pinned script is deleted once, by main()'s own `finally`, not per call site


# ============================================================================================ CP6-T13


def t13_no_circular_import():
    print("CP6-T13: run_records.py has no circular import with ratchet.py")
    check("[CP6-T13] run_records.py exists", RUN_RECORDS.exists())
    if not RUN_RECORDS.exists():
        return
    rr_src = RUN_RECORDS.read_text()
    try:
        rr_tree = ast.parse(rr_src, filename="run_records.py")
    except SyntaxError as e:
        check("[CP6-T13] run_records.py parses as valid Python", False, str(e))
        return

    module_level_ratchet_imports = []
    for n in rr_tree.body:
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "ratchet" for a in n.names):
            module_level_ratchet_imports.append(ast.get_source_segment(rr_src, n))
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "ratchet":
            module_level_ratchet_imports.append(ast.get_source_segment(rr_src, n))
    check("[CP6-T13] run_records.py never imports ratchet.py back at module load time (no import cycle, by its "
          "own top-level import statements)", not module_level_ratchet_imports, module_level_ratchet_imports)

    code = ("import sys\n"
            "sys.path.insert(0, %r)\n" % str(SCRIPT_DIR) +
            "assert 'ratchet' not in sys.modules, 'ratchet.py was already imported before run_records'\n"
            "import run_records\n"
            "assert 'ratchet' not in sys.modules, 'importing run_records alone must not pull in ratchet'\n"
            "import ratchet\n"
            "print('ALL_OK')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                       cwd=str(_tmp.mkdtemp(prefix="cp6-t13-")))
    ok = r.returncode == 0 and "ALL_OK" in r.stdout
    check("[CP6-T13] importing run_records.py alone in a fresh interpreter (ratchet.py never imported first) "
          "succeeds with no ImportError, and ratchet.py's own later import of run_records succeeds too", ok,
          (r.returncode, r.stdout[-300:], r.stderr[-500:]))


# ============================================================================================ CP6-T14


def t14_no_new_global_mutable_registry():
    print("CP6-T14: the move adds no new module-level mutable registry")
    check("[CP6-T14] run_records.py exists", RUN_RECORDS.exists())
    if not RUN_RECORDS.exists():
        return
    rr_src = RUN_RECORDS.read_text()
    try:
        rr_tree = ast.parse(rr_src, filename="run_records.py")
    except SyntaxError as e:
        check("[CP6-T14] run_records.py parses as valid Python", False, str(e))
        return

    mutable_ctor_names = {"dict", "list", "set", "Counter", "defaultdict", "OrderedDict"}
    offenders = []
    for n in rr_tree.body:
        if isinstance(n, ast.Assign):
            v = n.value
            is_mutable_literal = isinstance(v, (ast.Dict, ast.List, ast.Set))
            is_mutable_call = isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in mutable_ctor_names
            if is_mutable_literal or is_mutable_call:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        offenders.append(t.id)
    check("[CP6-T14] no new module-level mutable dict/list/set is introduced at the top level of "
          "run_records.py: none of these 25 names kept a pre-existing registry of its own, so there is no "
          "sanctioned exception to make for this move", offenders == [], offenders)

    global_stmt_users = []
    rr_defs = top_level_defs(rr_tree)
    for name in FUNCS:
        node = rr_defs.get(name)
        if node is None:
            continue
        if any(isinstance(sub, ast.Global) for sub in ast.walk(node)):
            global_stmt_users.append(name)
    check("[CP6-T14] none of the moved functions uses a `global` statement to keep state of its own between "
          "calls", not global_stmt_users, global_stmt_users)

    # _IN_HOOK is the one pre-existing module-level flag some hook-env helpers read (require_hook_env); it must
    # stay a single definition in ratchet.py, never duplicated into run_records.py too.
    live_src = live_ratchet_source()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
    except SyntaxError as e:
        check("[CP6-T14] the current ratchet.py parses as valid Python", False, str(e))
        return
    live_has_in_hook = assign_value_dump(live_tree, "_IN_HOOK") is not None
    rr_has_in_hook = assign_value_dump(rr_tree, "_IN_HOOK") is not None
    check("[CP6-T14] _IN_HOOK (pre-existing) stays a single definition -- in ratchet.py, not duplicated into "
          "run_records.py too", live_has_in_hook and not rr_has_in_hook, (live_has_in_hook, rr_has_in_hook))


# ============================================================================================ CP6-T15


def t15_hook_handlers_stayed_in_ratchet():
    print("CP6-T15: the hook handlers themselves stayed in ratchet.py, not moved")
    names = ["run_hook", "decide_prompt", "decide_guard", "decide_stop", "emit_hook", "normalize_hook",
             "read_heartbeat", "_heartbeat", "_heartbeat_path", "hooks_status", "cmd_doctor", "cmd_hooks_waive"]
    live_src = live_ratchet_source()
    try:
        live_defs = top_level_defs(ast.parse(live_src, filename="ratchet.py"))
    except SyntaxError as e:
        check("[CP6-T15] the current ratchet.py parses as valid Python", False, str(e))
        return
    missing_in_ratchet = [n for n in names if n not in live_defs]
    check("[CP6-T15] every hook handler name is still defined at top level in ratchet.py",
          not missing_in_ratchet, missing_in_ratchet)

    check("[CP6-T15] run_records.py exists", RUN_RECORDS.exists())
    if not RUN_RECORDS.exists():
        return
    try:
        rr_defs = top_level_defs(ast.parse(RUN_RECORDS.read_text(), filename="run_records.py"))
    except SyntaxError as e:
        check("[CP6-T15] run_records.py parses as valid Python", False, str(e))
        return
    leftover_in_run_records = [n for n in names if n in rr_defs]
    check("[CP6-T15] none of the hook handler names is defined in run_records.py (this move stayed out of the "
          "hooks section, out of scope)", not leftover_in_run_records, leftover_in_run_records)


# ============================================================================================ CP6-T16


def t16_dynamic_load_exposes_moved_functions():
    print("CP6-T16: dynamically loading ratchet.py as a module still exposes every moved function by name")
    names = ["record_hook_env", "hook_env_problem", "register_active", "choice_letter", "message_intent",
             "hooks_waive_intent", "is_listed_yes", "approval_snapshot", "integrity_problems", "verify_integrity",
             "_set_blocked", "ratchet_home", "hooks_installed_anywhere", "hooks_in_use", "require_hook_env"]
    spec = importlib.util.spec_from_file_location("ratchet_cp6_t16", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    missing = [n for n in names if not hasattr(m, n)]
    check("[CP6-T16] every one of the 15 named functions is present as a module attribute on the dynamically "
          "loaded ratchet.py (the explicit-name import re-exports it)", not missing, missing)

    check("[CP6-T16] run_records.py exists", RUN_RECORDS.exists())
    if not RUN_RECORDS.exists() or missing:
        return
    spec2 = importlib.util.spec_from_file_location("run_records_cp6_t16", RUN_RECORDS)
    rr = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(rr)
    battery = [
        ("choice_letter", ("B", ["A", "B"])),
        ("message_intent", ("yes",)),
        ("hooks_waive_intent", ("yes, go ahead without hooks",)),
        ("is_listed_yes", ("yes",)),
    ]
    for name, args in battery:
        got_m = getattr(m, name)(*args)
        got_rr = getattr(rr, name)(*args)
        check(f"[CP6-T16] m.{name}{args} returns the same result as calling run_records.{name} directly with "
              "the same arguments", got_m == got_rr, (name, args, got_rr, got_m))


# ============================================================================================ CP6-T17..T19
# Structural/targeted checks only, per .ratchet/learnings.md's director steer: this checkpoint's own
# tests.command already runs all ten named regression files for real (and the heavy pre-existing suites are run
# once, project-wide, by `ratchet.py complete`); nesting a full re-run of any of them here would double real
# time for no extra coverage.


def t17_ten_named_regression_files_structural():
    print("CP6-T17: the ten named regression files are present, unedited, and the specific module-attribute "
          "call sites the plan names still resolve through the dynamically loaded ratchet.py")
    files_and_calls = [
        ("tests/test_hooks.py", []),
        ("tests/test_hooks_waiver.py", []),
        ("tests/test_trust_cp1.py", []),
        ("tests/test_trust_cp4.py", ["record_hook_env"]),
        ("tests/test_trust_cp5_a.py", ["_set_blocked"]),
        ("tests/test_trust_cp5_b.py", []),
        ("tests/test_trust_cp5_c.py", []),
        ("tests/test_lanes_start_extra.py", []),
        ("tests/test_lanes_request.py", []),
        ("tests/test_signing.py", []),
    ]
    for relpath, call_names in files_and_calls:
        p = ROOT / relpath
        check(f"[CP6-T17] {relpath} exists", p.exists())
        if not p.exists():
            continue
        src = p.read_text()
        for cname in call_names:
            check(f"[CP6-T17] {relpath} still calls `m.{cname}(...)` through the dynamically loaded ratchet.py "
                  "module (module-attribute access, which the explicit-name re-export satisfies -- no "
                  "re-pointing needed for this file)", f"m.{cname}(" in src, cname)
    check("[CP6-T17] this checkpoint's own tests.command already runs all ten files for real, alongside this "
          "file, in the same run_all.py invocation -- this case only confirms the specific call sites the plan "
          "names still resolve through module-attribute access", True)


def t18_cp4_x14_caller_scan_unaffected():
    print("CP6-T18: test_trust_cp4.py's human_override/log_override caller-set scan (CP4-X06/X14) is unaffected "
          "by this move")
    live_src = live_ratchet_source()
    try:
        live_defs = top_level_defs(ast.parse(live_src, filename="ratchet.py"))
    except SyntaxError as e:
        check("[CP6-T18] the current ratchet.py parses as valid Python", False, str(e))
        return
    names = ["_require_approval", "cmd_accept_state", "cmd_init", "cmd_choice"]
    missing = [n for n in names if n not in live_defs]
    check("[CP6-T18] none of CP4-X14's 4 call sites (_require_approval, cmd_accept_state, cmd_init, cmd_choice) "
          "is one of CP6's moved names, so all 4 stay defined in ratchet.py after the move", not missing, missing)
    rr_leftover = []
    if RUN_RECORDS.exists():
        rr_leftover = [n for n in names if n in top_level_defs(ast.parse(RUN_RECORDS.read_text()))]
    check("[CP6-T18] ... and none of them is defined in run_records.py either (the move stayed inside its "
          "declared boundary)", not rr_leftover, rr_leftover)
    try:
        cp4_src = (ROOT / "tests" / "test_trust_cp4.py").read_text()
    except OSError as e:
        check("[CP6-T18] setup: tests/test_trust_cp4.py is readable", False, str(e))
        return
    check("[CP6-T18] tests/test_trust_cp4.py still contains its own CP4-X14 structural scan (the "
          "human_override/log_override call-site collection the plan's finding this check proves applies to)",
          'elif node.func.id == "log_override"' in cp4_src and "log_callers.append(fn.name)" in cp4_src)


def t19_cp4_lock_and_cp5_providers_smoke_unaffected():
    print("CP6-T19: CP4's lock-contention area and CP5's `providers` smoke call are unaffected by this move")
    current = cp5y_load_engine()
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    with engine_env(env):
        p = current._markers_path()
        if p.exists():
            p.unlink()
        lock_path = current._record_lock_path(p)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(f"{os.getpid() + 1} another-process")
        res = current.update_record(p, lambda d: {**d, "x": {"slug": "smoke"}}, wait=0.5)
        lock_path.unlink(missing_ok=True)
    check("[CP6-T19] CP4's lock-contention area (update_record/_record_lock_path, from records.py) still "
          "refuses cleanly ('busy') when a live lock is held, unaffected by CP6's own move",
          res == "busy", res)
    d = g.project("cp6t19")
    r = g.rx(d, "providers", "--json")
    check("[CP6-T19] CP5's `ratchet.py providers` smoke call (from provider_discovery.py) still succeeds, "
          "unaffected by CP6's own move", r.returncode == 0, (r.returncode, r.stdout[-300:], r.stderr[-300:]))


# ============================================================================================ CP6-T20


def t20_full_suite_passes():
    print("CP6-T20: the complete local suite passes after this checkpoint")
    # See .ratchet/learnings.md (2026-10-20, "Final resolution"): this case does not read evidence/CP6/
    # regression.log at all by default, and does not nest another full run of tests/run_all.py either -- this
    # is already structurally enforced by `ratchet.py complete`, which refuses to mark a checkpoint done when
    # its own regression run fails. Set RATCHET_CP6_T20_FULL_SUITE=1 to actually pay for running it inline.
    if os.environ.get("RATCHET_CP6_T20_FULL_SUITE") == "1":
        # Strip the opt-in flag from the child's environment: tests/run_all.py's own run reaches this same
        # test_modularize_cp6.py again, and if it inherited the flag it would take this same full-suite branch
        # too, recursing until a timeout or suite-lock contention instead of completing one verification run.
        child_env = {k: v for k, v in os.environ.items() if k != "RATCHET_CP6_T20_FULL_SUITE"}
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=5400, env=child_env)
        m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
        failed = int(m.group(2)) if m else -1
        check("[CP6-T20] python3 tests/run_all.py exits 0 with 0 failed", r.returncode == 0 and failed == 0,
              (r.returncode, r.stdout[-400:]))
        return
    check("[CP6-T20] done-means #5 (full suite passes) is enforced structurally by `ratchet.py complete`, which "
          "refuses to mark a checkpoint done when its own regression run fails -- not by this test case "
          "re-reading complete's own evidence file; set RATCHET_CP6_T20_FULL_SUITE=1 to actually run the full "
          "suite inline instead", True)


# ============================================================================================ CP6-X01 (adversarial round 1 fix)


def _top_level_assigns(tree):
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value
    return out


def _normalized_dump(node):
    """ast.dump() of `node`, but with a List/Set/Dict literal folded to the same shape as an equivalent
    Tuple/frozenset(...)/types.MappingProxyType(...) of the same elements -- so a name whose shape changed from
    a plain list/set/dict into an equivalent immutable tuple/frozenset/MappingProxyType when it was copied into
    run_records.py (exactly what round-1's own fix did for 6 of the 11 names this move's new-mutable-registry
    scan would otherwise have flagged) still counts as the SAME value for the dead-duplicate check below. Round
    2's host-correctness/A2 finding: comparing raw ast.dump() values (the original design) caught only the 5
    names whose shape did NOT change, missing the 6 that did."""
    if isinstance(node, ast.Call):
        fn = node.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if fname == "MappingProxyType" and len(node.args) == 1 and isinstance(node.args[0], ast.Dict):
            return _normalized_dump(node.args[0])
        if fname in ("frozenset", "set", "tuple", "list") and len(node.args) == 1 \
                and isinstance(node.args[0], (ast.Set, ast.List, ast.Tuple)):
            elts = node.args[0].elts
            return ("set", tuple(sorted(_normalized_dump(e) for e in elts))) if fname in ("frozenset", "set") \
                else ("seq", tuple(_normalized_dump(e) for e in elts))
    if isinstance(node, ast.Set):
        return ("set", tuple(sorted(_normalized_dump(e) for e in node.elts)))
    if isinstance(node, (ast.List, ast.Tuple)):
        return ("seq", tuple(_normalized_dump(e) for e in node.elts))
    if isinstance(node, ast.Dict):
        return ("map", tuple(sorted((_normalized_dump(k), _normalized_dump(v))
                                     for k, v in zip(node.keys, node.values))))
    return ast.dump(node)


def _dead_duplicates(check_tree, rr_assigns):
    """Every top-level name assigned in BOTH `check_tree` and run_records.py, with the same value once
    List/Set/Dict-vs-Tuple/frozenset/MappingProxyType shape differences are normalized away, and no remaining
    Load of that name anywhere else in `check_tree`."""
    check_assigns = _top_level_assigns(check_tree)
    out = []
    for name, check_value in check_assigns.items():
        rr_value = rr_assigns.get(name)
        if rr_value is None:
            continue  # not also defined in run_records.py: not this case's concern
        if _normalized_dump(check_value) != _normalized_dump(rr_value):
            continue  # same name, different value: not a copy-with-no-remaining-reader
        # Any Load of this name anywhere else in check_tree (the Assign's own Store doesn't count) means it is
        # still read there, so a same-named, same-valued pair in run_records.py is fine (e.g. two modules that
        # happen to need an identical small literal, not a hand-synced copy of the real, moved vocabulary).
        readers = [n for n in ast.walk(check_tree)
                   if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)]
        if not readers:
            out.append(name)
    return out


def t21_dead_duplicate_constants_flagged():
    print("CP6-X01: a module-level constant copied into run_records.py, with no remaining reader left in "
          "ratchet.py, is flagged the same way a leftover def/class is (round-1 adversarial finding A2: CP6-T01's "
          "own leftover scan only ever checked for a stray def/class, which is exactly how finding A1's 11 dead "
          "duplicate constants slipped through unnoticed)")
    live_src = live_ratchet_source()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
    except SyntaxError as e:
        check("[CP6-X01] the current ratchet.py parses as valid Python", False, str(e))
        return
    check("[CP6-X01] run_records.py exists", RUN_RECORDS.exists())
    if not RUN_RECORDS.exists():
        return
    rr_src = RUN_RECORDS.read_text()
    try:
        rr_tree = ast.parse(rr_src, filename="run_records.py")
    except SyntaxError as e:
        check("[CP6-X01] run_records.py parses as valid Python", False, str(e))
        return
    rr_assigns = _top_level_assigns(rr_tree)

    dead_duplicates = _dead_duplicates(live_tree, rr_assigns)
    check("[CP6-X01] no top-level constant in ratchet.py is a byte-identical, unread duplicate of one "
          "run_records.py already owns", not dead_duplicates, sorted(dead_duplicates))

    # Round-2 host-correctness/A2's own self-proof: build a reader-free fixture out of the 11 names' ORIGINAL
    # (pre-move) shapes -- a plain dict/list/set literal, not yet the equivalent immutable tuple/frozenset/
    # MappingProxyType round 1's fix copied them into run_records.py as -- and confirm the scan above flags
    # every one of them, not just the 5 whose shape happened not to change.
    eleven = ["HOOK_ENV_RECORDS", "_HOOK_ENV_REC_RE", "_HOOK_ENV_CORE", "_HOOK_ENV_TIE_RANK", "APPROVAL_PHRASES",
              "BENIGN_PHRASES", "HEDGE_WORDS", "APPROVAL_FILLERS", "CHOICE_RE", "HOOKS_WAIVE_PHRASES", "RULE_KEYS"]
    try:
        pinned_src = pinned_ratchet_source()
        pinned_tree = ast.parse(pinned_src, filename="ratchet.py@pre-move")
    except Exception as e:
        check("[CP6-X01] setup: the pinned pre-move ratchet.py source is readable and parses", False, str(e))
        return
    pinned_assigns = _top_level_assigns(pinned_tree)
    missing = [n for n in eleven if n not in pinned_assigns]
    check("[CP6-X01] setup: all 11 known dead-duplicate names are real top-level assigns in the pinned "
          "pre-move source", not missing, missing)
    if missing:
        return
    fixture_src = "\n".join(f"{n} = {ast.get_source_segment(pinned_src, pinned_assigns[n])}" for n in eleven)
    fixture_tree = ast.parse(fixture_src, filename="cp6_x01_fixture.py")
    flagged = _dead_duplicates(fixture_tree, rr_assigns)
    check("[CP6-X01] the scan above, run against the 11 names' own pre-move (pre-shape-change) definitions, "
          "flags all 11 -- not just the 5 whose shape happened not to change when they were copied",
          sorted(flagged) == sorted(eleven), sorted(flagged))


# ============================================================================================ CP6-X02


def t22_bind_host_first_binder_wins():
    print("CP6-X02: a SECOND ratchet.py instance loaded in-process (deliberately, or through a lazy "
          "_ratchet_module() reach started while an earlier instance is already bound) never steals "
          "require_hook_env's view of the already-bound instance's own _IN_HOOK (round-2 adversarial findings "
          "codex-standards/X1, host-alt-correctness/A1, host-correctness/A1: round-1's bind_host() was "
          "last-writer-wins, so whichever instance loaded most recently anywhere in the process silently won, "
          "even for an unrelated call on an older, still-live instance)")
    prior_host = run_records._HOST
    prior_ratchet = sys.modules.get("ratchet")
    try:
        run_records._HOST = None
        sys.modules.pop("ratchet", None)
        m = trust_helpers.engine()  # a fresh instance under a throwaway name; its own top-level code binds it
        d = Path(_tmp.mkdtemp(prefix="cp6-x02-"))
        (d / ".ratchet").mkdir()
        _plant_crash_marker(d)  # unresolved crash + no hook-env.json: hook_env_problem(d) refuses unless bypassed
        m._IN_HOOK = True  # the way run_hook really sets it, on the instance actually in the middle of a hook

        raised_before = False
        try:
            m.require_hook_env(d)
        except SystemExit:
            raised_before = True
        check("[CP6-X02] setup: m.require_hook_env(d) bypasses (no refusal) before any lazy reach, since "
              "m._IN_HOOK is True", not raised_before)

        # The real order host-correctness/A1 showed actually breaks: a lazy _ratchet_module() reach (here,
        # through run_records._current_hook_env()'s own director_key_path() call) happens BEFORE the next
        # require_hook_env() call, not after -- and, since 'ratchet' was never registered for `m` (it was
        # loaded under a throwaway name), that reach loads a brand-new THIRD instance and runs its own
        # top-level code, which also calls bind_host().
        run_records._current_hook_env()
        check("[CP6-X02] setup: the lazy reach really did register a NEW module under sys.modules['ratchet'], "
              "distinct from m (the scenario that broke round 1's fix)",
              sys.modules.get("ratchet") is not None and sys.modules.get("ratchet") is not m)

        raised_after = False
        try:
            m.require_hook_env(d)
        except SystemExit:
            raised_after = True
        check("[CP6-X02] m.require_hook_env(d) still bypasses after the lazy reach loaded a second instance "
              "elsewhere in the process -- m's own _IN_HOOK is still True, and nothing else's load can "
              "silently repoint what this call sees", not raised_after)
    finally:
        run_records._HOST = prior_host
        if prior_ratchet is None:
            sys.modules.pop("ratchet", None)
        else:
            sys.modules["ratchet"] = prior_ratchet


TESTS = [
    ("CP6-T01", t01_module_byte_identical_and_explicit_imports),
    ("CP6-T02", t02_help_byte_identical),
    ("CP6-T03", t03_status_json_byte_identical),
    ("CP6-T04", t04_hook_reply_byte_identical),
    ("CP6-T05", t05_failure_exit_codes_unchanged),
    ("CP6-T06", t06_hook_env_round_trip_unchanged),
    ("CP6-T07", t07_doctor_hooks_fields_unchanged),
    ("CP6-T08", t08_hook_missing_record_refusal_unchanged),
    ("CP6-T09", t09_hook_stale_record_refusal_unchanged),
    ("CP6-T10", t10_two_runs_same_project_busy_message_unchanged),
    ("CP6-T11", t11_reply_classifiers_battery_unchanged),
    ("CP6-T12", t12_integrity_check_and_approve_changes_unchanged),
    ("CP6-T13", t13_no_circular_import),
    ("CP6-T14", t14_no_new_global_mutable_registry),
    ("CP6-T15", t15_hook_handlers_stayed_in_ratchet),
    ("CP6-T16", t16_dynamic_load_exposes_moved_functions),
    ("CP6-T17", t17_ten_named_regression_files_structural),
    ("CP6-T18", t18_cp4_x14_caller_scan_unaffected),
    ("CP6-T19", t19_cp4_lock_and_cp5_providers_smoke_unaffected),
    ("CP6-T20", t20_full_suite_passes),
    ("CP6-X01", t21_dead_duplicate_constants_flagged),
    ("CP6-X02", t22_bind_host_first_binder_wins),
]


def main():
    # Write the ONE shared pinned pre-move script now, before any case runs, and delete it exactly once below
    # (in this `finally`, so it's cleaned up even if a case crashes) -- see _write_pinned_script()'s docstring.
    # This is the single write/delete exposure window for the whole run, instead of one per call site.
    pinned_path = _write_pinned_script()
    try:
        for cid, fn in TESTS:
            try:
                fn()
            except Exception as e:  # a crash in this file still counts as a (failed) result for this case
                global FAIL
                FAIL += 1
                print(f"  ✗ [{cid}] CRASHED: {e}")
                traceback.print_exc()
    finally:
        pinned_path.unlink(missing_ok=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
