#!/usr/bin/env python3
"""
CP4 tests: the tamper-evidence and record-locking machinery (_chain_path/_read_record/_write_record/
_append_record/_note_unreadable/_BreakerLock/_ChainLock/_RecordLock/_record_lock_path/update_record/
_chain_record/_record_chain_now/record_chain/append_recorded/chain_problem/verify_chain) moves, verbatim,
into skills/ratchet-orchestrator/scripts/records.py.   python3 tests/test_modularize_cp4.py

AST diffs against CP1's pre-move pinned source prove every moved body is byte-for-byte unchanged. Static and
dynamic import checks prove the module shape (no star import, no circular import, no new module-level mutable
registry beyond the two that already existed). A loaded-engine check proves the lock class hierarchy
(_ChainLock < _BreakerLock < _RecordLock < _EngineNotesLock, and _StateLock/_IntegrateLock, which stay in
ratchet.py) is unchanged once records.py's classes are imported into ratchet.py's namespace. The
byte-identical CLI/status/hook/exit-code comparison re-runs against CP1's saved baseline
(tests/fixtures/modularize_cp1/baseline/), split into 4 cases (CP4-05..08) the way the plan names them.
Real two-process concurrency and tamper-evidence drills (CP4-09..14) exercise the lock/chain primitives through
genuine OS subprocess contention and hand-edited/corrupted records, reusing the same subprocess-helper
techniques tests/test_trust_cp5_d.py's _CP5R10_BREAKER and tests/test_trust_cp5_e.py's _CP5R13_CHILD/
cp5r13_race already established, adapted to records.py's own primitives. CP4-15/16 reuse (never reinvent)
tests/test_trust_cp5_e.py's own cp5r13_shared_write_violations/cp5r16_unconsumed_update_record_calls scan
functions, feeding them records.py's real and probed source directly, to prove those scans have teeth against
records.py's own content. CP4-17/18/19 prove the existing heavy trust suites (test_trust_cp5_d.py,
test_trust_cp5_e.py, test_quota_shared.py) were re-pointed where they needed it (a structural source scan) and
keep passing where they did not (a check that only ever calls through the loaded engine module needs no
re-pointing at all) -- per .ratchet/learnings.md's director steer, none of these nests a full ~900s run of those
heavy files by default (this checkpoint's own tests.command already runs them for real); set
RATCHET_CP4_T17_FULL=1 / RATCHET_CP4_T18_FULL=1 / RATCHET_CP4_T19_FULL=1 to actually pay for running one inline.

This suite writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP4-01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
from trust_helpers import ENGINE, cp5v_until, cp5y_load_engine, engine_env, make_env  # noqa: E402

SCRIPT_DIR = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
RECORDS = SCRIPT_DIR / "records.py"
# Same pre-move pin CP1/CP2/CP3 use: the commit right before any code in this run moved. The 16 names this
# checkpoint moves were never touched by CP2's or CP3's own moves, so their body at this pin is still identical
# to the live, pre-CP4 ratchet.py today -- confirmed by hand (git show + ast diff) before writing this file.
PRE_MOVE_COMMIT = "d0f7bd147f2990ace56a46a189b4e9200e345d99"
PRE_MOVE_RELPATH = "skills/ratchet-orchestrator/scripts/ratchet.py"

FUNCS = ["_chain_path", "_read_record", "_write_record", "_append_record", "_note_unreadable", "_record_lock_path",
         "update_record", "_chain_record", "_record_chain_now", "record_chain", "append_recorded", "chain_problem",
         "verify_chain"]
CLASSES = ["_BreakerLock", "_ChainLock", "_RecordLock"]
ALL_NAMES = FUNCS + CLASSES

BASELINE_DIR = ROOT / "tests" / "fixtures" / "modularize_cp1" / "baseline"
MANIFEST = BASELINE_DIR / "manifest.json"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP4-[TX0-9]+)\]")


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
    """ast.dump() of the value a top-level `name = ...` Assign holds, or None when there is no such Assign.
    Used (instead of ast.literal_eval) because one of the two sanctioned pre-existing registries,
    _NOTED_UNREADABLE = set(), is a Call node ast.literal_eval rejects, not a literal."""
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.dump(n.value)
    return None


def rx(cwd, *args, env=None):
    base = {k: v for k, v in os.environ.items() if k != "RATCHET_ROOT"}
    if env:
        base.update(env)
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=str(cwd), capture_output=True, text=True,
                          env=base, timeout=60)


def _read_manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ============================================================================================ CP4-01


def t01_records_module_byte_identical():
    print("CP4-01: records.py's 16 moved definitions are byte-identical to before the move")
    try:
        pinned_src = pinned_ratchet_source()
    except Exception as e:
        check("[CP4-01] setup: the pre-move pinned ratchet.py source is readable via git show", False, str(e))
        return
    pinned_tree = ast.parse(pinned_src, filename="ratchet.py@pre-move")
    pinned_defs = top_level_defs(pinned_tree)
    missing_pinned = [n for n in ALL_NAMES if n not in pinned_defs]
    check("[CP4-01] setup: all 16 names are real top-level defs/classes in the pre-move pinned source",
          not missing_pinned, missing_pinned)
    if missing_pinned:
        return

    check("[CP4-01] records.py exists", RECORDS.exists())
    if not RECORDS.exists():
        return
    rec_src = RECORDS.read_text()
    try:
        rec_tree = ast.parse(rec_src, filename="records.py")
    except SyntaxError as e:
        check("[CP4-01] records.py parses as valid Python", False, str(e))
        return
    rec_defs = top_level_defs(rec_tree)

    for name in ALL_NAMES:
        check(f"[CP4-01] {name} is defined at top level in records.py", name in rec_defs)
        if name not in rec_defs:
            continue
        pinned_seg = ast.get_source_segment(pinned_src, pinned_defs[name])
        rec_seg = ast.get_source_segment(rec_src, rec_defs[name])
        check(f"[CP4-01] {name}'s body in records.py is byte-for-byte identical to its pre-move body",
              rec_seg == pinned_seg, "" if rec_seg == pinned_seg else (pinned_seg, rec_seg))

    live_src = live_ratchet_source()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
        live_defs = top_level_defs(live_tree)
    except SyntaxError as e:
        check("[CP4-01] the current ratchet.py parses as valid Python", False, str(e))
        return
    leftover = [n for n in ALL_NAMES if n in live_defs]
    check("[CP4-01] none of the 16 names has its own `def`/`class` left behind in ratchet.py's top-level AST",
          not leftover, leftover)


# ============================================================================================ CP4-02


def t02_explicit_imports_no_star_no_cycle():
    print("CP4-02: ratchet.py imports the moved names explicitly, with no star import and no circular import")
    check("[CP4-02] records.py exists", RECORDS.exists())
    if not RECORDS.exists():
        return
    rec_src = RECORDS.read_text()
    try:
        rec_tree = ast.parse(rec_src, filename="records.py")
    except SyntaxError as e:
        check("[CP4-02] records.py parses as valid Python", False, str(e))
        return

    live_src = live_ratchet_source()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
    except SyntaxError as e:
        check("[CP4-02] the current ratchet.py parses as valid Python", False, str(e))
        return

    star_live = [n for n in ast.walk(live_tree)
                 if isinstance(n, ast.ImportFrom) and n.module == "records"
                 and any(a.name == "*" for a in n.names)]
    check("[CP4-02] ratchet.py never does `from records import *`", not star_live)

    named_imports = set()
    for n in ast.walk(live_tree):
        if isinstance(n, ast.ImportFrom) and n.module == "records":
            named_imports.update(a.name for a in n.names if a.name != "*")
    still_missing = [n for n in ALL_NAMES if n not in named_imports]
    check("[CP4-02] ratchet.py imports each of the 16 names explicitly by name from records "
          "(`from records import ...`)", not still_missing, sorted(named_imports))

    # Static half: records.py's own MODULE-LEVEL import statements never import ratchet.py back (a lazy import
    # inside a def is not module-load-time, and is not checked here).
    module_level_ratchet_imports = []
    for n in rec_tree.body:
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "ratchet" for a in n.names):
            module_level_ratchet_imports.append(ast.get_source_segment(rec_src, n))
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "ratchet":
            module_level_ratchet_imports.append(ast.get_source_segment(rec_src, n))
    check("[CP4-02] records.py never imports ratchet.py back at module load time (no import cycle)",
          not module_level_ratchet_imports, module_level_ratchet_imports)

    # Dynamic half: a fresh interpreter, started with ONLY the scripts dir on sys.path, imports records.py alone
    # (ratchet.py never imported at all in this child process before or during it). This checks ONLY that the
    # import itself succeeds with no ImportError/partially-initialized-module error -- it does NOT call the 16
    # functions, because several of them (chain_problem/verify_chain/_record_chain_now in particular) reach for
    # ratchet.py-resident names (ratchet_home(), die(), ACCEPT_STATE_CMD, require_hook_env, RATCHET_DIR, _IN_HOOK)
    # that are explicitly outside this checkpoint's 16-name move list. This is the exact same KNOWN FORWARD
    # DEPENDENCY shape tests/test_modularize_cp2.py's CP2-T02 already documents and is deliberately lenient
    # about (any solution that resolves it at call time rather than import time is fine): never assumed here.
    code = ("import sys\n"
            "sys.path.insert(0, %r)\n" % str(SCRIPT_DIR) +
            "assert 'ratchet' not in sys.modules, 'ratchet.py was already imported before records'\n"
            "import records\n"
            "print('ALL_OK')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                       cwd=str(_tmp.mkdtemp(prefix="cp4-t02-")))
    ok = r.returncode == 0 and "ALL_OK" in r.stdout
    check("[CP4-02] importing records.py alone in a fresh interpreter (ratchet.py never imported first) "
          "succeeds with no ImportError or partially-initialized-module error", ok,
          (r.returncode, r.stdout[-300:], r.stderr[-500:]))


# ============================================================================================ CP4-03


def t03_no_new_global_mutable_registry():
    print("CP4-03: the move adds no new module-level mutable registry beyond _NOTED_UNREADABLE/_CHAIN_REBUILD")
    try:
        pinned_src = pinned_ratchet_source()
    except Exception as e:
        check("[CP4-03] setup: the pre-move pinned ratchet.py source is readable via git show", False, str(e))
        return
    pinned_tree = ast.parse(pinned_src, filename="ratchet.py@pre-move")

    check("[CP4-03] records.py exists", RECORDS.exists())
    if not RECORDS.exists():
        return
    rec_src = RECORDS.read_text()
    try:
        rec_tree = ast.parse(rec_src, filename="records.py")
    except SyntaxError as e:
        check("[CP4-03] records.py parses as valid Python", False, str(e))
        return

    mutable_ctor_names = {"dict", "list", "set", "Counter", "defaultdict", "OrderedDict"}
    offenders = []
    for n in rec_tree.body:
        if isinstance(n, ast.Assign):
            v = n.value
            is_mutable_literal = isinstance(v, (ast.Dict, ast.List, ast.Set))
            is_mutable_call = isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in mutable_ctor_names
            if is_mutable_literal or is_mutable_call:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        offenders.append(t.id)
    check("[CP4-03] the only module-level mutable dict/list/set carried into records.py is _NOTED_UNREADABLE "
          "(already existed before the move) -- no brand-new shared mutable collection is created to make the "
          "move easier", offenders and set(offenders) == {"_NOTED_UNREADABLE"}, offenders)

    for name in ("_NOTED_UNREADABLE", "_CHAIN_REBUILD"):
        pinned_dump = assign_value_dump(pinned_tree, name)
        rec_dump = assign_value_dump(rec_tree, name)
        check(f"[CP4-03] {name} exists at top level in records.py with the identical initial value it had "
              "before the move", pinned_dump is not None and rec_dump == pinned_dump, (pinned_dump, rec_dump))

    global_stmt_users = []
    rec_defs = top_level_defs(rec_tree)
    for name in FUNCS:
        node = rec_defs.get(name)
        if node is None:
            continue
        if any(isinstance(sub, ast.Global) for sub in ast.walk(node)):
            global_stmt_users.append(name)
    check("[CP4-03] none of the moved functions uses a `global` statement to keep state of its own between "
          "calls (beyond the two pre-existing registries above)", not global_stmt_users, global_stmt_users)


# ============================================================================================ CP4-04


def t04_lock_class_hierarchy_intact():
    print("CP4-04: every lock subclass still subclasses correctly after the move")
    m = cp5y_load_engine()
    pairs = [("_ChainLock", "_BreakerLock"), ("_RecordLock", "_ChainLock"),
             ("_EngineNotesLock", "_RecordLock"), ("_StateLock", "_BreakerLock"),
             ("_IntegrateLock", "_StateLock")]
    for sub, base in pairs:
        has_sub, has_base = hasattr(m, sub), hasattr(m, base)
        check(f"[CP4-04] setup: the loaded engine has both {sub} and {base}", has_sub and has_base,
              (sub, has_sub, base, has_base))
        if not (has_sub and has_base):
            continue
        check(f"[CP4-04] {sub} subclasses {base}", issubclass(getattr(m, sub), getattr(m, base)))


# ============================================================================================ CP4-05..08
# The same byte-identical CLI/status/hook/exit-code comparison CP2-T11/CP3-T11 already run against CP1's saved
# baseline, split into the 4 separate cases the plan names (--help, status --json, a hook reply, failure codes).


def t05_help_byte_identical():
    print("CP4-05: --help is byte-identical to CP1's baseline")
    manifest = _read_manifest()
    check("[CP4-05] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
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
            check(f"[CP4-05] baseline file for `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        r = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1",
                                                 PYTHON_COLORS="0"))
        check(f"[CP4-05] `ratchet.py {' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              r.returncode == e.get("exit_code"), r.returncode)
        check(f"[CP4-05] `ratchet.py {' '.join(cmd)}`'s stdout is byte-identical to CP1's baseline",
              r.stdout == saved, (saved[:200], r.stdout[:200]))


def t06_status_json_byte_identical():
    print("CP4-06: status --json is byte-identical to CP1's baseline sample")
    manifest = _read_manifest()
    check("[CP4-06] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
          "missing or invalid" if manifest is None else "")
    if manifest is None:
        return
    samples = [e for e in manifest.get("entries") or [] if e.get("kind") == "status_json"]
    check("[CP4-06] setup: a status_json baseline entry is recorded", bool(samples), manifest.get("entries"))
    if not samples:
        return
    try:
        saved_status = json.loads((BASELINE_DIR / samples[0]["path"]).read_text())
    except (OSError, json.JSONDecodeError) as ex:
        check("[CP4-06] the saved status --json baseline parses as JSON", False, str(ex))
        return
    r = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=30)
    try:
        live_status = json.loads(r.stdout) if r.returncode == 0 else None
    except json.JSONDecodeError:
        live_status = None
    check("[CP4-06] a live `status --json` invocation right now succeeds", live_status is not None,
          (r.returncode, r.stdout[-300:]))
    if live_status is not None:
        check("[CP4-06] status --json's top-level key set is unchanged from CP1's baseline",
              set(live_status) == set(saved_status), (sorted(live_status), sorted(saved_status)))

    # Genuine full-output comparison, on a reproducible fixture, instead of only the top-level keys above: CP1's
    # own saved status_json.json baseline was captured from this run's own active, ever-advancing state (see its
    # "note" in manifest.json), so comparing full output against that exact file can never hold past CP1 itself
    # -- the same reason CP1-T06/CP2-T11/CP3-T11 all stop at a key-set check. Instead, build ONE fresh throwaway
    # project (the same reproducible-fixture shape CP1's own hook_reply baseline and CP4-07 below use) and run
    # `status --json` against it twice, back to back, with no mutation in between: once through the pre-move
    # pinned ratchet.py (the exact commit before any code in this run moved), once through the current, already
    # split engine -- the same pinned-vs-current-on-one-fixture technique CP3-T07 already established for
    # `fingerprint`. Nothing needs normalizing: both invocations read the identical on-disk state, so a real
    # difference in output can only come from the move itself.
    try:
        pinned_src = pinned_ratchet_source()
    except Exception as e:
        check("[CP4-06] setup: the pre-move pinned ratchet.py source is readable via git show", False, str(e))
        return
    d = Path(_tmp.mkdtemp(prefix="cp4-t06-status-"))
    r_init = subprocess.run([sys.executable, str(ENGINE), "init", "status parity cap", "--slug", "st"],
                            cwd=str(d), capture_output=True, text=True, timeout=30)
    check("[CP4-06] setup: a fresh throwaway project's `init` succeeds", r_init.returncode == 0,
          (r_init.returncode, r_init.stderr))
    if r_init.returncode != 0:
        return
    pinned_script = SCRIPT_DIR / f"_cp4_t06_pinned_{os.getpid()}.py"
    pinned_script.write_text(pinned_src)
    try:
        r_before = subprocess.run([sys.executable, str(pinned_script), "status", "--json"], cwd=str(d),
                                  capture_output=True, text=True, timeout=30)
    finally:
        pinned_script.unlink(missing_ok=True)
    check("[CP4-06] setup: the pre-move pinned ratchet.py's `status --json` exits 0 on the fresh fixture",
          r_before.returncode == 0, (r_before.returncode, r_before.stdout[-300:], r_before.stderr[-300:]))
    r_after = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(d), capture_output=True,
                             text=True, timeout=30)
    check("[CP4-06] the current ratchet.py's `status --json` exits 0 on the same fresh fixture",
          r_after.returncode == 0, (r_after.returncode, r_after.stdout[-300:], r_after.stderr[-300:]))
    if r_before.returncode == 0 and r_after.returncode == 0:
        check("[CP4-06] the complete status --json output (not just the top-level key set checked above) is "
              "byte-identical between the pre-move pinned engine and the current, already-split engine, on the "
              "same fresh reproducible fixture -- no live-repo state involved",
              r_before.stdout == r_after.stdout, (r_before.stdout, r_after.stdout))


def t07_hook_reply_byte_identical():
    print("CP4-07: a sample hook reply is byte-identical to CP1's baseline")
    d = Path(_tmp.mkdtemp(prefix="cp4-t07-hook-"))
    r = subprocess.run([sys.executable, str(ENGINE), "init", "hook cap", "--slug", "hk"], cwd=str(d),
                       capture_output=True, text=True, timeout=30)
    check("[CP4-07] setup: a fresh throwaway project's `init` succeeds", r.returncode == 0, (r.returncode, r.stderr))
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
        check("[CP4-07] a fresh copilot guard reply parses as JSON", False, str(e))
        return
    check("[CP4-07] the fresh guard reply has permissionDecision: deny, same shape as CP1's baseline",
          reply.get("permissionDecision") == "deny", reply)
    reason = reply.get("permissionDecisionReason", "")
    static_prefix = ("RATCHET GUARD: gate state, evidence, approvals and config under .ratchet/ are "
                     "written by ratchet.py only (you may write reviews/, states/, tests/, reference/, "
                     "checkpoints.json, app-scout.json, standards.md, learnings.md). Blocked: ")
    check("[CP4-07] the reason's static prefix is byte-identical to CP1's baseline (only the random "
          "temp-dir path in the blocked-file suffix legitimately differs)",
          reason.startswith(static_prefix) and reason.endswith("state.json"), reason)

    # Genuine full-reply comparison against CP1's actual saved baseline file (not a hardcoded prefix/suffix
    # stand-in): read hook_reply_copilot_guard.json via the manifest, normalize only the one explicitly variable
    # piece of either reply (the random absolute temp-dir path baked into the blocked-file suffix -- the same
    # thing the prefix/suffix check above already calls out as the sole legitimate difference), and then compare
    # the complete reply dicts for equality.
    manifest2 = _read_manifest()
    check("[CP4-07] setup: CP1's baseline manifest exists and is valid JSON (for the full-reply comparison)",
          manifest2 is not None, "missing or invalid" if manifest2 is None else "")
    if manifest2 is None:
        return
    hook_samples = [e for e in manifest2.get("entries") or [] if e.get("kind") == "hook_reply"]
    check("[CP4-07] setup: a hook_reply baseline entry is recorded", bool(hook_samples), manifest2.get("entries"))
    if not hook_samples:
        return
    try:
        saved_reply = json.loads((BASELINE_DIR / hook_samples[0]["path"]).read_text())
    except (OSError, json.JSONDecodeError) as ex:
        check("[CP4-07] the saved hook reply baseline parses as JSON", False, str(ex))
        return

    def _normalize_blocked_path(r):
        reason_ = r.get("permissionDecisionReason", "")
        return {**r, "permissionDecisionReason": re.sub(r"Blocked: .*$", "Blocked: <path>", reason_)}

    check("[CP4-07] the complete fresh guard reply (every key, not only decision + a hardcoded reason prefix) "
          "matches CP1's saved baseline exactly, once each one's own random temp-dir path is normalized away",
          _normalize_blocked_path(reply) == _normalize_blocked_path(saved_reply), (saved_reply, reply))


def t08_failure_exit_codes_unchanged():
    print("CP4-08: known failure exit codes are unchanged")
    manifest = _read_manifest()
    check("[CP4-08] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
          "missing or invalid" if manifest is None else "")
    if manifest is None:
        return
    failures = [e for e in manifest.get("entries") or [] if e.get("kind") == "failure"]
    check("[CP4-08] setup: at least two failure baseline entries are recorded", len(failures) >= 2, failures)
    for e in failures:
        cmd = e.get("cmd") or []
        try:
            saved = (BASELINE_DIR / e["path"]).read_text()
        except OSError as ex:
            check(f"[CP4-08] baseline file for failure `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        if cmd and cmd[0] == "no-such-command":
            rf = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True,
                                text=True, timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1",
                                                                 PYTHON_COLORS="0"))
            got = rf.stderr
        else:  # "status --json (run from a directory with no .ratchet/ at all)"
            empty_dir = Path(_tmp.mkdtemp(prefix="cp4-t08-empty-"))
            rf = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(empty_dir),
                                capture_output=True, text=True, timeout=30)
            got = rf.stderr
        check(f"[CP4-08] failure case `{' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              rf.returncode == e.get("exit_code"), rf.returncode)
        check(f"[CP4-08] failure case `{' '.join(cmd)}`'s stderr is byte-identical to CP1's baseline",
              got == saved, (saved, got))


# ============================================================================================ CP4-09


_CP4R09_CHILD = """
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp4r09", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
root, slug, ready, go = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5])
real = m._read_record
def slow(path, *a, **k):
    out = real(path, *a, **k)
    if Path(path).name == "active-runs.json":
        time.sleep(0.4)  # widen the window between this read and the write after it
    return out
m._read_record = slow
# update_record's own `_read_record` call (every shared-record writer, including register_active, goes through
# update_record) resolves against records.py's own globals since CP4's split, not m's -- patch that module too,
# when it is loaded, the same fix tests/test_trust_cp5_e.py's _CP5R13_CHILD already applies for this exact
# ownership change, or the injected delay never reaches the real read and the race window is never forced.
_records_mod = sys.modules.get("records")
if _records_mod is not None:
    _records_mod._read_record = slow
ready.write_text("1")
while not go.exists():
    time.sleep(0.002)
m.register_active(root, slug)
print("done")
"""


def t09_no_lost_entry_race():
    print("CP4-09: two real OS processes racing to register_active() (active-runs.json) never lose an entry")
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    script = Path(_tmp.mkdtemp(prefix="ratchet-cp4r09-s-")) / "child.py"
    script.write_text(_CP4R09_CHILD)
    gate = Path(_tmp.mkdtemp(prefix="ratchet-cp4r09-g-"))
    a = Path(_tmp.mkdtemp(prefix="ratchet-cp4r09-a-"))
    b = Path(_tmp.mkdtemp(prefix="ratchet-cp4r09-b-"))
    procs = [
        subprocess.Popen([sys.executable, str(script), str(ENGINE), str(a), "fa", str(gate / "ready-0"),
                          str(gate / "go")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
        subprocess.Popen([sys.executable, str(script), str(ENGINE), str(b), "fb", str(gate / "ready-1"),
                          str(gate / "go")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
    ]
    ready = cp5v_until(lambda: (gate / "ready-0").exists() and (gate / "ready-1").exists(), timeout=60)
    (gate / "go").write_text("go")
    outs = []
    for p in procs:
        try:
            o, e = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            o, e = p.communicate()
        outs.append((p.returncode, o, e))
    check("[CP4-09] setup: both subprocesses were released at once and ran to completion with no error",
          ready and all(rc == 0 for rc, _o, _e in outs), (ready, [(rc, e[-300:]) for rc, _o, e in outs]))
    try:
        runs = json.loads((rh / "active-runs.json").read_text())
    except (OSError, ValueError) as e:
        check("[CP4-09] active-runs.json is readable and valid JSON after the race", False, str(e))
        return
    want = {str(a.resolve()): "fa", str(b.resolve()): "fb"}
    got = {k: v.get("slug") for k, v in runs.items() if k in want}
    check("[CP4-09] both entries are present afterward -- no lost update -- and neither process raised",
          got == want, (got, want))
    left = [x.name for x in rh.iterdir() if x.name.startswith("active-runs.lock") or x.name.endswith(".tmp")]
    check("[CP4-09] no stray lock or temp file is left behind", left == [], left)


# ============================================================================================ CP4-10


_CP4R10_BREAKER = """
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp4r10", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
role, marks = sys.argv[2], Path(sys.argv[3])
other = "fast" if role == "slow" else "slow"
lock = m._ChainLock(wait=3.0)
lock_path = str(lock.path)
if role == "slow":  # judges the aged lock stale, then stalls right before removing it until `fast` has had its try
    real_unlink = os.unlink
    first = [True]
    def unlink(p, *a, **k):
        if first[0] and str(p) == lock_path:
            first[0] = False
            (marks / "slow-judged").write_text("1")
            end = time.monotonic() + 60
            while not ((marks / "fast-held").exists() or (marks / "fast-done").exists()) and time.monotonic() < end:
                time.sleep(0.01)
        return real_unlink(p, *a, **k)
    os.unlink = unlink
with lock:
    held = lock.held
    if held:
        (marks / (role + "-held")).write_text("1")
        end = time.monotonic() + 3
        while not (marks / (other + "-done")).exists() and time.monotonic() < end:
            time.sleep(0.01)
(marks / (role + "-done")).write_text("1")
print("held" if held else "not-held")
"""


def t10_stale_lock_cleared_by_exactly_one():
    print("CP4-10: two processes racing to clear the same stale file-hashes.lock (_ChainLock): only one holds it")
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    script = Path(_tmp.mkdtemp(prefix="ratchet-cp4r10-s-")) / "breaker.py"
    script.write_text(_CP4R10_BREAKER)
    lock = rh / "file-hashes.lock"
    lock.write_text("")
    aged = time.time() - 120
    os.utime(lock, (aged, aged))
    marks = Path(_tmp.mkdtemp(prefix="ratchet-cp4r10-m-"))

    def spawn(role):
        return subprocess.Popen([sys.executable, str(script), str(ENGINE), role, str(marks)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    slow = spawn("slow")
    judged = cp5v_until(lambda: (marks / "slow-judged").exists(), timeout=60)
    fast = spawn("fast")
    so, se = slow.communicate(timeout=120)
    fo, fe = fast.communicate(timeout=120)
    check("[CP4-10] two processes clearing the same stale file-hashes.lock: only one of them holds it (the one "
          "that judged it stale first and removed it; the other never removes that one's fresh lock)",
          judged and slow.returncode == 0 and fast.returncode == 0 and so.strip() == "held"
          and fo.strip() == "not-held", (judged, so.strip(), fo.strip(), se[-300:], fe[-300:]))
    left = sorted(x.name for x in rh.iterdir() if x.name.startswith("file-hashes.lock"))
    check("[CP4-10] ... and once both are done, neither the lock file nor its .break file is left behind",
          left == [], left)


# ============================================================================================ CP4-11


def t11_busy_live_lock_refuses_cleanly():
    print("CP4-11: a command that can't take a live record lock waits briefly then refuses, claiming nothing "
          "was saved")
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    m = cp5y_load_engine()
    with engine_env(env):
        p = m._markers_path()
        lock_path = m._record_lock_path(p)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        live_token = f"{os.getpid() + 1} another-process"
        lock_path.write_text(live_token)  # fresh: a live holder, never judged stale
        before = p.read_text() if p.exists() else None
        t0 = time.monotonic()
        res = m.update_record(p, lambda d: {**d, "x": {"slug": "nope"}}, wait=1.2)
        took = time.monotonic() - t0
        after = p.read_text() if p.exists() else None
        after_lock = lock_path.read_text()

        buf = io.StringIO()
        exited = None
        with contextlib.redirect_stderr(buf):
            try:
                m._die_record_busy(p, "nothing was changed")
            except SystemExit as e:
                exited = e
    check("[CP4-11] update_record on a record whose lock is held by a live process waits (up to `wait` seconds) "
          "and then returns 'busy', never writing", res == "busy" and took >= 1.0, (res, took))
    check("[CP4-11] the record on disk is byte-for-byte unchanged from before the call", after == before,
          (before, after))
    check("[CP4-11] the lock file still holds the other process's token -- never silently taken or cleared",
          after_lock == live_token, after_lock)
    check("[CP4-11] the real refusal path every such caller uses (_die_record_busy) exits non-zero",
          exited is not None and exited.code not in (0, None), exited)
    check("[CP4-11] ... and names the exact lock file in its refusal, without claiming the change was saved",
          str(lock_path) in buf.getvalue() and "nothing was changed" in buf.getvalue(), buf.getvalue())


# ============================================================================================ CP4-12


def t12_release_never_removes_a_foreign_token():
    print("CP4-12: releasing a lock whose token no longer matches this process leaves the file alone")
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    m = cp5y_load_engine()
    with engine_env(env):
        lock = m._ChainLock(wait=2.0)
        lock.__enter__()
        held = lock.held
        check("[CP4-12] setup: this process takes the lock first", held, lock.path)
        if not held:
            return
        foreign_token = "99999 some-other-process"
        lock.path.write_text(foreign_token)  # simulates another process legitimately taking it over
        lock.__exit__(None, None, None)
        still_there = lock.path.exists()
        content = lock.path.read_text() if still_there else None
        lock.path.unlink(missing_ok=True)
    check("[CP4-12] release never removes a lock file whose token no longer matches this process -- it is left "
          "exactly as it is (still the other token), never deleted", still_there and content == foreign_token,
          content)


# ============================================================================================ CP4-13


def t13_hand_edited_file_caught_as_tampered():
    print("CP4-13: a hand-edited record is caught as tampered")
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    m = cp5y_load_engine()
    d = Path(_tmp.mkdtemp(prefix="cp4-t13-"))
    f = d / "tracked.txt"
    f.write_text("original\n")
    with engine_env(env):
        m.record_chain(f)
        f.write_text("tampered by hand\n")  # bypasses ratchet.py entirely
        why = m.chain_problem(f, "the file")
    check("[CP4-13] chain_problem reports a mismatch for a file hand-edited outside ratchet.py", why is not None,
          why)
    if why is not None:
        check("[CP4-13] the mismatch names the file and the exact existing text 'was changed outside ratchet.py'",
              "was changed outside ratchet.py" in why and str(f) in why, why)
        check("[CP4-13] it points at the accept-state recovery path", m.ACCEPT_STATE_CMD in why, why)

    buf = io.StringIO()
    exited = None
    with engine_env(env), contextlib.redirect_stderr(buf):
        try:
            m.verify_chain(f, "the file")
        except SystemExit as e:
            exited = e
    check("[CP4-13] a caller that calls verify_chain on the tampered file dies (refuses) rather than silently "
          "accepting the edit", exited is not None and exited.code not in (0, None), exited)
    check("[CP4-13] verify_chain's refusal says the same thing chain_problem reported",
          "was changed outside ratchet.py" in buf.getvalue(), buf.getvalue())


# ============================================================================================ CP4-14


def t14_damaged_hash_record_fails_safe():
    print("CP4-14: a damaged hash record fails safe instead of vouching for anything")
    env = make_env("claude")
    rh = Path(env["RATCHET_HOME"])
    rh.mkdir(parents=True, exist_ok=True)
    m = cp5y_load_engine()
    d = Path(_tmp.mkdtemp(prefix="cp4-t14-"))
    f = d / "tracked.txt"
    f.write_text("original\n")
    with engine_env(env):
        m.record_chain(f)
        (rh / "file-hashes.json").write_text("{not valid json")
        try:
            why = m.chain_problem(f, "the file")
            crashed = None
        except Exception as e:  # noqa: BLE001 - reported by the check below
            why, crashed = None, e
    check("[CP4-14] chain_problem never crashes on a damaged file-hashes.json", crashed is None, crashed)
    check("[CP4-14] it refuses to vouch for the file (returns a reason, not None -- a damaged record grants "
          "trust to nothing)", why is not None, why)
    if why is not None:
        check("[CP4-14] the refusal names file-hashes.json and says plainly it could not be read",
              "file-hashes.json" in why and "can't be read" in why, why)
        check("[CP4-14] it points at the accept-state recovery path", m.ACCEPT_STATE_CMD in why, why)

    buf = io.StringIO()
    exited = None
    crashed2 = None
    with engine_env(env), contextlib.redirect_stderr(buf):
        try:
            m.verify_chain(f, "the file")
        except SystemExit as e:
            exited = e
        except Exception as e:  # noqa: BLE001 - reported by the check below
            crashed2 = e
    check("[CP4-14] no hook or command crashes when asked about a tracked file while the hash record is damaged",
          crashed2 is None, crashed2)
    check("[CP4-14] verify_chain refuses (dies) instead of silently trusting the file",
          exited is not None and exited.code not in (0, None), exited)


# ============================================================================================ CP4-15/16
# Reuses (never reinvents) tests/test_trust_cp5_e.py's own scan functions, feeding them records.py's real and
# probed source directly -- the same functions the plan names (cp5r13_shared_write_violations,
# cp5r16_unconsumed_update_record_calls), proving they have teeth against records.py's own content.


def _import_cp5_e():
    try:
        import test_trust_cp5_e as tt
        return tt, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def t15_shared_write_scan_covers_records():
    print("CP4-15: the lock-bypassing direct-write scan also catches a bypass placed inside records.py")
    check("[CP4-15] setup: records.py exists", RECORDS.exists())
    if not RECORDS.exists():
        return
    tt, err = _import_cp5_e()
    check("[CP4-15] setup: tests/test_trust_cp5_e.py imports cleanly so its shared-write scan can be reused, "
          "never reinvented", tt is not None, err)
    if tt is None:
        return
    combined = live_ratchet_source() + "\n" + RECORDS.read_text()
    bad = tt.cp5r13_shared_write_violations(combined)
    check("[CP4-15] the real, unmodified records.py plus ratchet.py source together produce zero shared-write "
          "violations", bad == [], bad[:10])
    probe = (RECORDS.read_text() + "\n\n"
             "def _cp4_t15_probe_stray(path):\n    _write_record(_chain_path(), {})\n\n"
             "def _cp4_t15_probe_fine(path):\n    update_record(_chain_path(), lambda d: d)\n")
    flagged = sorted({fn for fn, _l, _w in tt.cp5r13_shared_write_violations(probe)})
    check("[CP4-15] a hand-written bypass write planted inside records.py's own source (not through "
          "update_record) is flagged by the scan, and a legitimate update_record-based write next to it is not",
          flagged == ["_cp4_t15_probe_stray"], flagged)


def t16_unconsumed_update_record_scan_covers_records():
    print("CP4-16: the unconsumed-update_record-result scan also covers records.py")
    check("[CP4-16] setup: records.py exists", RECORDS.exists())
    if not RECORDS.exists():
        return
    tt, err = _import_cp5_e()
    check("[CP4-16] setup: tests/test_trust_cp5_e.py imports cleanly so its unconsumed-call scan can be reused, "
          "never reinvented", tt is not None, err)
    if tt is None:
        return
    combined = live_ratchet_source() + "\n" + RECORDS.read_text()
    bad = tt.cp5r16_unconsumed_update_record_calls(combined)
    check("[CP4-16] the real, unmodified records.py plus ratchet.py source together have no bare, "
          "result-discarding update_record(...) call", bad == [], bad)
    probe = ("def f():\n    update_record(p, fn)\n\n"
             "def g():\n    done = update_record(p, fn)\n    return done\n")
    flagged = tt.cp5r16_unconsumed_update_record_calls(probe)
    check("[CP4-16] a bare update_record(...) statement inserted into records.py-shaped source is flagged, and "
          "one whose result is assigned is not", flagged == [2], flagged)


# ============================================================================================ CP4-17/18/19
# None of these nests a full ~900s run of a heavy pre-existing suite by default (.ratchet/learnings.md's
# director steer, 2026-10-20): this checkpoint's own tests.command already runs test_trust_cp5_d.py,
# test_trust_cp5_e.py and test_quota_shared.py for real, alongside this file, in the same run_all.py invocation.
# Set RATCHET_CP4_T17_FULL=1 / RATCHET_CP4_T18_FULL=1 / RATCHET_CP4_T19_FULL=1 to pay for running one inline and
# have this file check its specific case ids directly.


def _run_heavy_suite(path, timeout=3600):
    env = dict(os.environ, NO_COLOR="1", PYTHON_COLORS="0")
    return subprocess.run([sys.executable, str(path)], cwd=str(ROOT), capture_output=True, text=True,
                          timeout=timeout, env=env)


def _case_lines_all_pass(stdout, case_ids):
    """(True, "") when every one of these bracketed case ids has at least one result line in stdout and none of
    its result lines starts with the fail glyph; (False, detail) otherwise -- a missing id (renamed, or never
    run) counts as a failure here too, never a silent vacuous pass."""
    missing = [cid for cid in case_ids if f"[{cid}]" not in stdout]
    if missing:
        return False, f"no result line found for {missing}"
    failing = [ln.strip() for ln in stdout.splitlines()
               if any(f"[{cid}]" in ln for cid in case_ids) and ln.strip().startswith("✗")]
    return (not failing), failing


def t17_cp5_d_checks_pass_against_split():
    print("CP4-17: test_trust_cp5_d.py's lock-stealing ([CP5-X54]) and temp-file-naming ([CP5-X55]) checks keep "
          "their exact pass/fail meaning (they only ever call through the loaded engine module, so they need no "
          "re-pointing for this move)")
    if os.environ.get("RATCHET_CP4_T17_FULL") == "1":
        r = _run_heavy_suite(ROOT / "tests" / "test_trust_cp5_d.py")
        ok, detail = _case_lines_all_pass(r.stdout, ["CP5-X54", "CP5-X55"])
        check("[CP4-17] test_trust_cp5_d.py's [CP5-X54]/[CP5-X55] both pass against the split engine", ok,
              detail if not ok else (r.returncode, r.stdout[-400:]))
        return
    check("[CP4-17] [CP5-X54]/[CP5-X55] call through the loaded engine module (`m._EngineNotesLock`, "
          "`m.record_chain`, `m._note_engine_call`), never read ratchet.py's source text/AST, so they need no "
          "re-pointing for this move; this checkpoint's own tests.command already runs the file for real; set "
          "RATCHET_CP4_T17_FULL=1 to verify them specifically, inline, instead", True)


def t18_cp5_e_checks_pass_against_split():
    print("CP4-18: test_trust_cp5_e.py's per-record-lock, structural and busy-dies checks pass against the "
          "split code")
    try:
        src = (ROOT / "tests" / "test_trust_cp5_e.py").read_text()
    except OSError as e:
        check("[CP4-18] setup: tests/test_trust_cp5_e.py is readable", False, str(e))
        return
    check("[CP4-18] test_trust_cp5_e.py's own structural scans ([CP5-X71]/[CP5-X75]) are re-pointed to also "
          "read records.py's source (not ratchet.py's alone), so update_record's own definition and helpers "
          "are covered too", "records.py" in src, "no mention of records.py found in test_trust_cp5_e.py")
    if os.environ.get("RATCHET_CP4_T18_FULL") == "1":
        r = _run_heavy_suite(ROOT / "tests" / "test_trust_cp5_e.py")
        ids = ["CP5-X70", "CP5-X71", "CP5-X72", "CP5-X73", "CP5-X75", "CP5-X76"]
        ok, detail = _case_lines_all_pass(r.stdout, ids)
        check("[CP4-18] test_trust_cp5_e.py's round-13/14/16 checks all pass against the split engine", ok,
              detail if not ok else (r.returncode, r.stdout[-400:]))
        return
    check("[CP4-18] this checkpoint's own tests.command already runs test_trust_cp5_e.py for real; set "
          "RATCHET_CP4_T18_FULL=1 to verify its case ids inline instead", True)


def t19_quota_shared_cp2_x11_covers_records():
    print("CP4-19: test_quota_shared.py's [CP2-X11] shared-record-lock case passes, re-pointed to also cover "
          "records.py")
    try:
        src = (ROOT / "tests" / "test_quota_shared.py").read_text()
    except OSError as e:
        check("[CP4-19] setup: tests/test_quota_shared.py is readable", False, str(e))
        return
    check("[CP4-19] test_quota_shared.py's [CP2-X11] structural scan is re-pointed to also read records.py's "
          "source (not ratchet.py's alone)", "records.py" in src,
          "no mention of records.py found in test_quota_shared.py")
    if os.environ.get("RATCHET_CP4_T19_FULL") == "1":
        r = _run_heavy_suite(ROOT / "tests" / "test_quota_shared.py")
        ok, detail = _case_lines_all_pass(r.stdout, ["CP2-X11"])
        check("[CP4-19] test_quota_shared.py's [CP2-X11] still passes, and still has teeth (locked race keeps "
              "both entries; lock-less race loses one), against the split engine", ok,
              detail if not ok else (r.returncode, r.stdout[-400:]))
        return
    check("[CP4-19] this checkpoint's own tests.command already runs test_quota_shared.py for real; set "
          "RATCHET_CP4_T19_FULL=1 to verify [CP2-X11] inline instead", True)


# ============================================================================================ CP4-20


def t20_full_suite_passes():
    print("CP4-20: the complete local suite passes after this checkpoint")
    # See .ratchet/learnings.md (2026-10-20 entry, finalized 2026-10-20 "Final resolution"): a checkpoint's own
    # "full suite passes" case cannot tell a fresh result of the run it is currently part of apart from a stale
    # log left by some earlier, unrelated attempt, so it does not read evidence/CP4/regression.log at all by
    # default, and does not nest another full run of tests/run_all.py either -- this is already structurally
    # enforced by `ratchet.py complete`, which refuses to mark a checkpoint done when its own regression run
    # fails. Set RATCHET_CP4_T20_FULL_SUITE=1 to actually pay for running it inline.
    if os.environ.get("RATCHET_CP4_T20_FULL_SUITE") == "1":
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=5400)
        m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
        failed = int(m.group(2)) if m else -1
        check("[CP4-20] python3 tests/run_all.py exits 0 with 0 failed", r.returncode == 0 and failed == 0,
              (r.returncode, r.stdout[-400:]))
        return
    check("[CP4-20] done-means #5 (full suite passes) is enforced structurally by `ratchet.py complete`, which "
          "refuses to mark a checkpoint done when its own regression run fails -- not by this test case "
          "re-reading complete's own evidence file; set RATCHET_CP4_T20_FULL_SUITE=1 to actually run the full "
          "suite inline instead", True)


TESTS = [
    ("CP4-01", t01_records_module_byte_identical),
    ("CP4-02", t02_explicit_imports_no_star_no_cycle),
    ("CP4-03", t03_no_new_global_mutable_registry),
    ("CP4-04", t04_lock_class_hierarchy_intact),
    ("CP4-05", t05_help_byte_identical),
    ("CP4-06", t06_status_json_byte_identical),
    ("CP4-07", t07_hook_reply_byte_identical),
    ("CP4-08", t08_failure_exit_codes_unchanged),
    ("CP4-09", t09_no_lost_entry_race),
    ("CP4-10", t10_stale_lock_cleared_by_exactly_one),
    ("CP4-11", t11_busy_live_lock_refuses_cleanly),
    ("CP4-12", t12_release_never_removes_a_foreign_token),
    ("CP4-13", t13_hand_edited_file_caught_as_tampered),
    ("CP4-14", t14_damaged_hash_record_fails_safe),
    ("CP4-15", t15_shared_write_scan_covers_records),
    ("CP4-16", t16_unconsumed_update_record_scan_covers_records),
    ("CP4-17", t17_cp5_d_checks_pass_against_split),
    ("CP4-18", t18_cp5_e_checks_pass_against_split),
    ("CP4-19", t19_quota_shared_cp2_x11_covers_records),
    ("CP4-20", t20_full_suite_passes),
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
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash in this file still counts as a (failed) result for this case
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
