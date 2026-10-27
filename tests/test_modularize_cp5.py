#!/usr/bin/env python3
"""
CP5 tests: provider discovery, probing, quota and fallback bookkeeping (probe_provider/_record_probe/
quota_failure/quota_retest_minutes/provider_share/bench_quota/record_round_failure/review_succeeded/
probe_due/probe_shared/update_review_starts/_reserve_under_lock/refund_review_start/provider_bench/
shared_unavailable/event_stream) moves, verbatim, into
skills/ratchet-orchestrator/scripts/provider_discovery.py.   python3 tests/test_modularize_cp5.py

AST diffs against CP1's pinned pre-move source prove every moved body is byte-for-byte unchanged, that
cmd_providers stays a thin CLI handler in ratchet.py, and that the module shape has no star import, no
circular import and no new module-level mutable registry. The byte-identical CLI/status/hook/exit-code
comparison re-runs the same pinned-vs-current-on-one-fixture technique CP3-T07/CP4-T06 already established
(no CP1 baseline exists for `providers` output specifically, so this file builds its own fresh,
reproducible fixture and runs it through the pre-move pinned engine and the current, already-split engine
side by side) plus CP1's saved baseline (tests/fixtures/modularize_cp1/baseline/) for --help/status/hook/
exit codes. Direct calls into the loaded engine module (cp5y_load_engine, the same technique
test_modularize_cp4.py used for its lock/race tests) prove reviewer ranking, provider_share's window limit,
probe_shared's claim-and-wait, a quota bench's own rules and record_round_failure's idempotency are
unchanged. CP5-T11 reuses (never reinvents) tests/test_trust_cp5_e.py's own cp5r13_shared_write_violations
scan, feeding it provider_discovery.py's real and probed source directly, the same pattern
test_modularize_cp4.py's CP4-15 already established for records.py.

Reuses tests/test_gates.py (`project`, `approve_plan`, `rx`, `ENV`, `say`), tests/test_discover.py
(`fake_opencode_bin`, `log_text`, `config_providers`) and tests/test_providers.py's fake-agent-CLI pattern
(`FAKE`, `fake_bin`) for the throwaway fixtures and fake provider CLIs, and tests/trust_helpers.py
(`ENGINE`, `cp5y_load_engine`, `engine_env`, `make_env`, `cp5v_until`) for the in-process engine calls and
real two-lane concurrency this checkpoint's own cases need.

This suite writes a per-test JSON report to $RATCHET_TEST_REPORT when that env var is set:
{"cases": [{"id": "CP5-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import ast
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402
import test_gates as g  # noqa: E402  (project, approve_plan, rx, ENV, say)
import test_discover as td  # noqa: E402  (fake_opencode_bin, log_text, config_providers)
import test_providers as tp  # noqa: E402  (FAKE fake-agent-CLI shell script, fake_bin)
from trust_helpers import ENGINE, cp5v_until, cp5y_load_engine, engine_env, make_env  # noqa: E402

SCRIPT_DIR = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
PROVIDER_DISCOVERY = SCRIPT_DIR / "provider_discovery.py"
# Same pre-move pin CP1/CP2/CP3/CP4 all use: the commit right before any code in this run moved. Confirmed by
# hand (git show + ast diff, before writing this file) that all 16 of this checkpoint's names are still
# top-level defs in THIS commit's ratchet.py, byte-for-byte identical to the live, pre-CP5 ratchet.py today --
# neither CP2, CP3 nor CP4's own moves ever touched any of these 16 names, so this one pin is still valid.
PRE_MOVE_COMMIT = "d0f7bd147f2990ace56a46a189b4e9200e345d99"
PRE_MOVE_RELPATH = "skills/ratchet-orchestrator/scripts/ratchet.py"

FUNCS = ["probe_provider", "_record_probe", "quota_failure", "quota_retest_minutes", "provider_share",
         "bench_quota", "record_round_failure", "review_succeeded", "probe_due", "probe_shared",
         "update_review_starts", "_reserve_under_lock", "refund_review_start", "provider_bench",
         "shared_unavailable", "event_stream"]
ALL_NAMES = list(FUNCS)  # this checkpoint moves no classes

BASELINE_DIR = ROOT / "tests" / "fixtures" / "modularize_cp1" / "baseline"
MANIFEST = BASELINE_DIR / "manifest.json"

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP5-[TX][0-9]+)\]")


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


def _read_manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ============================================================================================ CP5-T01


def t01_module_byte_identical():
    print("CP5-T01: provider_discovery.py's 16 moved definitions are byte-identical to before the move, and "
          "cmd_providers stays a thin CLI handler in ratchet.py")
    try:
        pinned_src = pinned_ratchet_source()
    except Exception as e:
        check("[CP5-T01] setup: the pre-move pinned ratchet.py source is readable via git show", False, str(e))
        return
    pinned_tree = ast.parse(pinned_src, filename="ratchet.py@pre-move")
    pinned_defs = top_level_defs(pinned_tree)
    missing_pinned = [n for n in ALL_NAMES if n not in pinned_defs]
    check("[CP5-T01] setup: all 16 names are real top-level defs in the pre-move pinned source",
          not missing_pinned, missing_pinned)
    if missing_pinned:
        return

    check("[CP5-T01] provider_discovery.py exists", PROVIDER_DISCOVERY.exists())
    if not PROVIDER_DISCOVERY.exists():
        return
    pd_src = PROVIDER_DISCOVERY.read_text()
    try:
        pd_tree = ast.parse(pd_src, filename="provider_discovery.py")
    except SyntaxError as e:
        check("[CP5-T01] provider_discovery.py parses as valid Python", False, str(e))
        return
    pd_defs = top_level_defs(pd_tree)

    for name in ALL_NAMES:
        check(f"[CP5-T01] {name} is defined at top level in provider_discovery.py", name in pd_defs)
        if name not in pd_defs:
            continue
        pinned_seg = ast.get_source_segment(pinned_src, pinned_defs[name])
        pd_seg = ast.get_source_segment(pd_src, pd_defs[name])
        check(f"[CP5-T01] {name}'s body in provider_discovery.py is byte-for-byte identical to its pre-move "
              "body", pd_seg == pinned_seg, "" if pd_seg == pinned_seg else (pinned_seg, pd_seg))

    live_src = live_ratchet_source()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
        live_defs = top_level_defs(live_tree)
    except SyntaxError as e:
        check("[CP5-T01] the current ratchet.py parses as valid Python", False, str(e))
        return
    leftover = [n for n in ALL_NAMES if n in live_defs]
    check("[CP5-T01] none of the 16 names has its own `def` left behind in ratchet.py's top-level AST",
          not leftover, leftover)
    check("[CP5-T01] cmd_providers is still defined at top level in ratchet.py (the thin CLI handler stays)",
          "cmd_providers" in live_defs, sorted(live_defs))


# ============================================================================================ CP5-T02


def t02_explicit_imports_no_star_no_cycle():
    print("CP5-T02: ratchet.py imports the moved names explicitly, with no star import and no circular import")
    check("[CP5-T02] provider_discovery.py exists", PROVIDER_DISCOVERY.exists())
    if not PROVIDER_DISCOVERY.exists():
        return
    pd_src = PROVIDER_DISCOVERY.read_text()
    try:
        pd_tree = ast.parse(pd_src, filename="provider_discovery.py")
    except SyntaxError as e:
        check("[CP5-T02] provider_discovery.py parses as valid Python", False, str(e))
        return

    live_src = live_ratchet_source()
    try:
        live_tree = ast.parse(live_src, filename="ratchet.py")
    except SyntaxError as e:
        check("[CP5-T02] the current ratchet.py parses as valid Python", False, str(e))
        return

    star_live = [n for n in ast.walk(live_tree)
                 if isinstance(n, ast.ImportFrom) and n.module == "provider_discovery"
                 and any(a.name == "*" for a in n.names)]
    check("[CP5-T02] ratchet.py never does `from provider_discovery import *`", not star_live)

    named_imports = set()
    for n in ast.walk(live_tree):
        if isinstance(n, ast.ImportFrom) and n.module == "provider_discovery":
            named_imports.update(a.name for a in n.names if a.name != "*")
    still_missing = [n for n in ALL_NAMES if n not in named_imports]
    check("[CP5-T02] ratchet.py imports each of the 16 names explicitly by name from provider_discovery "
          "(`from provider_discovery import ...`)", not still_missing, sorted(named_imports))

    # Static half: provider_discovery.py's own MODULE-LEVEL import statements never import ratchet.py back.
    module_level_ratchet_imports = []
    for n in pd_tree.body:
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "ratchet" for a in n.names):
            module_level_ratchet_imports.append(ast.get_source_segment(pd_src, n))
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "ratchet":
            module_level_ratchet_imports.append(ast.get_source_segment(pd_src, n))
    check("[CP5-T02] provider_discovery.py never imports ratchet.py back at module load time (no import "
          "cycle)", not module_level_ratchet_imports, module_level_ratchet_imports)

    # Dynamic half: a fresh interpreter, started with ONLY the scripts dir on sys.path, imports
    # provider_discovery.py alone (ratchet.py never imported at all in this child process). This checks ONLY
    # that the import itself succeeds -- several of these 16 functions reach for ratchet.py-resident names
    # (ratchet_home, _update_health, _health_path, die, ...) that stay outside this checkpoint's move; the
    # same documented, deliberately-lenient KNOWN FORWARD DEPENDENCY shape test_modularize_cp2.py's CP2-T02
    # and test_modularize_cp4.py's CP4-02 already establish (resolved at call time, never import time).
    #
    # After the standalone import, this also imports ratchet itself (simulating the real production order:
    # ratchet.py's own module body runs top to bottom -- including `from provider_discovery import ...` --
    # before main() ever calls anything, so "ratchet" is already in sys.modules by the time a moved function
    # runs) and then calls provider_bench(cfg, rid), one of the 16 moved functions, which reaches through
    # _reviewer_health_key -> provider_by_id -> _health_key -> provider_health, four of the lazy
    # _ratchet_module() forward dependencies, in one call. This proves the lazy loader genuinely resolves at
    # call time (not just that a bare `import provider_discovery` doesn't crash): a stale or broken forward
    # dependency would raise AttributeError/NameError here, not silently pass.
    code = ("import sys\n"
            "sys.path.insert(0, %r)\n" % str(SCRIPT_DIR) +
            "assert 'ratchet' not in sys.modules, 'ratchet.py was already imported before provider_discovery'\n"
            "import provider_discovery\n"
            "assert 'ratchet' not in sys.modules, 'importing provider_discovery alone must not pull in ratchet'\n"
            "import ratchet\n"
            "cfg = {'providers': [{'id': 'cp5-t02-probe', 'command': ['true'], 'company': 'acme'}]}\n"
            "bench = provider_discovery.provider_bench(cfg, 'cp5-t02-probe')\n"
            "assert bench is None, ('provider_bench should report no bench for a fresh provider', bench)\n"
            "print('ALL_OK')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                       cwd=str(_tmp.mkdtemp(prefix="cp5-t02-")))
    ok = r.returncode == 0 and "ALL_OK" in r.stdout
    check("[CP5-T02] importing provider_discovery.py alone in a fresh interpreter (ratchet.py never imported "
          "first) succeeds with no ImportError or partially-initialized-module error", ok,
          (r.returncode, r.stdout[-300:], r.stderr[-500:]))
    check("[CP5-T02] after that standalone import, importing ratchet and then calling provider_bench (one of "
          "the 16 moved functions) succeeds and returns a sane value via its lazy _ratchet_module() forward "
          "dependencies -- not just that the bare import statement doesn't crash", ok,
          (r.returncode, r.stdout[-300:], r.stderr[-500:]))


# ============================================================================================ CP5-T03


def t03_no_new_global_mutable_registry():
    print("CP5-T03: the move adds no new module-level mutable registry")
    check("[CP5-T03] provider_discovery.py exists", PROVIDER_DISCOVERY.exists())
    if not PROVIDER_DISCOVERY.exists():
        return
    pd_src = PROVIDER_DISCOVERY.read_text()
    try:
        pd_tree = ast.parse(pd_src, filename="provider_discovery.py")
    except SyntaxError as e:
        check("[CP5-T03] provider_discovery.py parses as valid Python", False, str(e))
        return

    mutable_ctor_names = {"dict", "list", "set", "Counter", "defaultdict", "OrderedDict"}
    offenders = []
    for n in pd_tree.body:
        if isinstance(n, ast.Assign):
            v = n.value
            is_mutable_literal = isinstance(v, (ast.Dict, ast.List, ast.Set))
            is_mutable_call = isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in mutable_ctor_names
            if is_mutable_literal or is_mutable_call:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        offenders.append(t.id)
    check("[CP5-T03] no module-level mutable dict/list/set is introduced at the top level of "
          "provider_discovery.py: none of these 16 functions kept a pre-existing registry of its own, so "
          "there is no sanctioned exception to make for this move", offenders == [], offenders)

    global_stmt_users = []
    pd_defs = top_level_defs(pd_tree)
    for name in FUNCS:
        node = pd_defs.get(name)
        if node is None:
            continue
        if any(isinstance(sub, ast.Global) for sub in ast.walk(node)):
            global_stmt_users.append(name)
    check("[CP5-T03] none of the moved functions uses a `global` statement to keep state of its own between "
          "calls", not global_stmt_users, global_stmt_users)


# ============================================================================================ CP5-T04
# No CP1 baseline was captured for `providers` output specifically (checked: manifest.json's entries are
# help/status_json/hook_reply/failure only) -- so this uses the same pinned-vs-current-on-one-fixture
# technique CP3-T07/CP4-T06 already established: run the identical command sequence against the pre-move
# pinned engine and the current, already-split engine, on two copies of the SAME fresh fixture, and compare
# byte-for-byte. Both the fixture's providers (a flat-rate preset and a per-use preset with the developer's
# recorded paid-provider OK) are driven through the fake `opencode` CLI test_discover.py already established.

MODEL_FLAT = "opencode-go/glm-5.3"          # flat-rate: needs no question at all
MODEL_PERUSE = "openrouter/z-ai/glm-5"      # per-use: needs the developer's exact paid-provider OK first


def _run_providers_fixture(engine_path, d, env, log_path):
    """Runs the one fixed command sequence (ask paid-provider, the exact reply, --add flat, --add per-use,
    plain list, --check all) against `engine_path` in project `d`. Returns a list of (label, returncode,
    stdout, stderr) tuples, one per command, in order."""
    def rx(*args, stdin=None):
        return subprocess.run([sys.executable, str(engine_path), *args], cwd=str(d), capture_output=True,
                              text=True, env=env, input=stdin, timeout=60)
    out = []
    ra = rx("ask", "paid-provider", "opencode", MODEL_PERUSE)
    out.append(("ask", ra.returncode, ra.stdout, ra.stderr))
    exact = None
    for pat in (r'reply exactly "([^"]+)"', r'reply[^".\n]*"([^"]+)"'):
        mm = re.search(pat, ra.stdout, re.I)
        if mm:
            exact = mm.group(1)
            break
    exact = exact or f"pay per use: {MODEL_PERUSE}"
    rs = g.say(d, exact)
    out.append(("say", rs.returncode, rs.stdout, rs.stderr))
    r1 = rx("providers", "--add", "oc-flat", "--preset", "opencode", "--model", MODEL_FLAT)
    out.append(("add-flat", r1.returncode, r1.stdout, r1.stderr))
    r2 = rx("providers", "--add", "oc-peruse", "--preset", "opencode", "--model", MODEL_PERUSE)
    out.append(("add-peruse", r2.returncode, r2.stdout, r2.stderr))
    r3 = rx("providers", "--json")
    out.append(("list", r3.returncode, r3.stdout, r3.stderr))
    r4 = rx("providers", "--check", "all")
    out.append(("check-all", r4.returncode, r4.stdout, r4.stderr))
    return out


def t04_providers_output_unchanged():
    print("CP5-T04: `ratchet.py providers` output (including --add) is byte-identical to before the move")
    b, models_file = td.fake_opencode_bin([MODEL_FLAT, MODEL_PERUSE])
    log_path = Path(_tmp.mkdtemp(prefix="cp5-t04-log-")) / "calls.log"
    env = dict(g.ENV)
    env["PATH"] = str(b)
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    env["RATCHET_OPENCODE_MODELS"] = str(models_file)
    env["RATCHET_OPENCODE_LOG"] = str(log_path)

    d_current = g.project("cp5t04cur")
    g.approve_plan(d_current)
    try:
        pinned_src = pinned_ratchet_source()
    except Exception as e:
        check("[CP5-T04] setup: the pre-move pinned ratchet.py source is readable via git show", False, str(e))
        return
    d_pinned = Path(_tmp.mkdtemp(prefix="cp5-t04-pinned-"))
    shutil.copytree(d_current, d_pinned, dirs_exist_ok=True)
    pinned_script = SCRIPT_DIR / f"_cp5_t04_pinned_{os.getpid()}.py.tmp"
    pinned_script.write_text(pinned_src)
    try:
        current_out = _run_providers_fixture(ENGINE, d_current, env, log_path)
        pinned_out = _run_providers_fixture(pinned_script, d_pinned, env, log_path)
    finally:
        pinned_script.unlink(missing_ok=True)

    check("[CP5-T04] setup: both runs produced the same number of command results",
          len(current_out) == len(pinned_out), (len(current_out), len(pinned_out)))
    for (label, cur_rc, cur_out, cur_err), (plabel, pin_rc, pin_out, pin_err) in zip(current_out, pinned_out):
        check(f"[CP5-T04] `{label}` exits the same code before and after the move", cur_rc == pin_rc,
              (label, cur_rc, pin_rc))
        check(f"[CP5-T04] `{label}`'s stdout is byte-identical before and after the move", cur_out == pin_out,
              (label, pin_out, cur_out))
        check(f"[CP5-T04] `{label}`'s stderr is byte-identical before and after the move", cur_err == pin_err,
              (label, pin_err, cur_err))

    _ISO_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+\+00:00")

    def _normalize_times(text):
        return _ISO_RE.sub("<at>", text) if text is not None else None

    for relpath in (".ratchet/config.json", ".ratchet/provider-status.json"):
        cp_, pp_ = d_current / relpath, d_pinned / relpath
        cur_text = _normalize_times(cp_.read_text() if cp_.exists() else None)
        pin_text = _normalize_times(pp_.read_text() if pp_.exists() else None)
        check(f"[CP5-T04] {relpath} ends up byte-for-byte the same before and after the move (the one "
              "genuinely variable piece -- each run's own test-request timestamp -- normalized away)",
              cur_text == pin_text, (relpath, pin_text, cur_text))


# ============================================================================================ CP5-T05
# --help, status --json, a hook reply and two known failure exit codes, byte-identical to CP1's saved
# baseline -- the same comparison CP2-T11/CP3-T11/CP4-05..08 already run.


def t05_help_status_hook_exit_codes_unchanged():
    print("CP5-T05: --help, status --json, a hook reply and failure exit codes are still byte-identical")
    manifest = _read_manifest()
    check("[CP5-T05] setup: CP1's baseline manifest exists and is valid JSON", manifest is not None,
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
            check(f"[CP5-T05] baseline file for `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        r = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1",
                                                 PYTHON_COLORS="0"))
        check(f"[CP5-T05] `ratchet.py {' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              r.returncode == e.get("exit_code"), r.returncode)
        check(f"[CP5-T05] `ratchet.py {' '.join(cmd)}`'s stdout is byte-identical to CP1's baseline",
              r.stdout == saved, (saved[:200], r.stdout[:200]))

    samples = [e for e in manifest.get("entries") or [] if e.get("kind") == "status_json"]
    check("[CP5-T05] setup: a status_json baseline entry is recorded", bool(samples), manifest.get("entries"))
    if samples:
        try:
            saved_status = json.loads((BASELINE_DIR / samples[0]["path"]).read_text())
        except (OSError, json.JSONDecodeError) as ex:
            check("[CP5-T05] the saved status --json baseline parses as JSON", False, str(ex))
            saved_status = None
        r = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=30)
        try:
            live_status = json.loads(r.stdout) if r.returncode == 0 else None
        except json.JSONDecodeError:
            live_status = None
        check("[CP5-T05] a live `status --json` invocation right now succeeds", live_status is not None,
              (r.returncode, r.stdout[-300:]))
        if live_status is not None and saved_status is not None:
            check("[CP5-T05] status --json's top-level key set is unchanged from CP1's baseline, including "
                  "that a benched provider still shows up the same way under unavailable_reviewers",
                  set(live_status) == set(saved_status), (sorted(live_status), sorted(saved_status)))

    # The two checks above only compare top-level KEYS against CP1's baseline (captured from this run's own
    # ever-changing active state, not a controlled fixture), so a regression in shared_unavailable's nested
    # unavailable_reviewers content -- exactly the function this checkpoint moves -- would not be caught.
    # This strengthens that: the SAME isolated project fixture, with one provider benched in the shared
    # health record, run through both the pre-move pinned engine and the current, already-split engine, with
    # their FULL status --json content compared (not just keys), normalizing only the genuinely variable
    # pieces (timestamps, each run's own temp project path).
    try:
        pinned_src_t05 = pinned_ratchet_source()
    except Exception as e:
        check("[CP5-T05] setup: the pre-move pinned ratchet.py source is readable via git show (full-content "
              "status --json comparison)", False, str(e))
        pinned_src_t05 = None
    if pinned_src_t05 is not None:
        # Reuses g.ENV as-is (never a separately pointed RATCHET_HOME) because the project's own hooks
        # already recorded g.ENV's RATCHET_HOME in .ratchet/hook-env.json at `init` time (the same
        # mismatch check tests/test_hooks.py exercises on purpose): a different RATCHET_HOME here would
        # make the guard refuse every later command against this fixture.
        d_base = g.project("cp5t05fix")
        g.approve_plan(d_base)
        health_env = dict(g.ENV)
        radd = subprocess.run([sys.executable, str(ENGINE), "providers", "--add", "p-bench", "--command",
                               json.dumps(["/bin/true", "{prompt}"]), "--model", "bench-model", "--company",
                               "acme", "--no-check"], cwd=str(d_base), capture_output=True, text=True,
                              env=health_env, timeout=30)
        check("[CP5-T05] setup: a hand-written provider is added to the isolated fixture (no probe, "
              "--no-check)", radd.returncode == 0, radd.stdout + radd.stderr)
        pv = None
        if radd.returncode == 0:
            fixture_cfg = json.loads((d_base / ".ratchet" / "config.json").read_text())
            pv = next((p for p in fixture_cfg.get("providers") or [] if p.get("id") == "p-bench"), None)
            check("[CP5-T05] setup: the added provider is really in the fixture's own config.json",
                  pv is not None, fixture_cfg.get("providers"))
        if pv is not None:
            m = cp5y_load_engine()
            with engine_env(health_env):
                key = m._health_key(pv)
                health_path = m._health_path()
            at = _dt.datetime.now(_dt.timezone.utc)
            until = at + _dt.timedelta(hours=3)
            health_path.parent.mkdir(parents=True, exist_ok=True)
            health_path.write_text(json.dumps({key: {
                "ok": False, "at": at.isoformat(timespec="microseconds"),
                "detail": "Rolling usage limit reached.",
                "bench": {"reason": "Rolling usage limit reached.",
                          "since": at.isoformat(timespec="microseconds"),
                          "until": until.isoformat(timespec="microseconds"), "quota": True}}}))

            d_cur = Path(_tmp.mkdtemp(prefix="cp5-t05-status-cur-"))
            d_pin = Path(_tmp.mkdtemp(prefix="cp5-t05-status-pin-"))
            shutil.copytree(d_base, d_cur, dirs_exist_ok=True)
            shutil.copytree(d_base, d_pin, dirs_exist_ok=True)
            pinned_script_t05 = SCRIPT_DIR / f"_cp5_t05_pinned_{os.getpid()}.py.tmp"
            pinned_script_t05.write_text(pinned_src_t05)
            try:
                r_cur = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(d_cur),
                                       capture_output=True, text=True, timeout=30, env=health_env)
                r_pin = subprocess.run([sys.executable, str(pinned_script_t05), "status", "--json"],
                                       cwd=str(d_pin), capture_output=True, text=True, timeout=30,
                                       env=health_env)
            finally:
                pinned_script_t05.unlink(missing_ok=True)
            check("[CP5-T05] setup: both the pinned pre-move engine and the current engine's `status --json` "
                  "succeed on the same isolated, benched-provider fixture", r_cur.returncode == 0 and
                  r_pin.returncode == 0, (r_cur.returncode, r_cur.stdout[-300:], r_pin.returncode,
                                          r_pin.stdout[-300:]))
            if r_cur.returncode == 0 and r_pin.returncode == 0:
                _ISO_RE_T05 = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:\+00:00|Z)?")

                def _normalize_status_t05(text, own_dir, other_dir):
                    t = _ISO_RE_T05.sub("<at>", text)
                    for p in (own_dir, own_dir.resolve(), other_dir, other_dir.resolve()):
                        t = t.replace(str(p), "<root>")
                    return t

                cur_norm = _normalize_status_t05(r_cur.stdout, d_cur, d_pin)
                pin_norm = _normalize_status_t05(r_pin.stdout, d_pin, d_cur)
                check("[CP5-T05] status --json's FULL content (not just its top-level keys) is "
                      "byte-identical before and after the move on the same isolated fixture, once "
                      "timestamps and each run's own temp project path are normalized away -- including "
                      "shared_unavailable's nested unavailable_reviewers content for the benched provider",
                      cur_norm == pin_norm, (pin_norm, cur_norm))
                try:
                    cur_json_t05 = json.loads(r_cur.stdout)
                except json.JSONDecodeError:
                    cur_json_t05 = {}
                down_t05 = cur_json_t05.get("unavailable_reviewers") or {}
                check("[CP5-T05] the isolated fixture's benched provider really shows up in "
                      "unavailable_reviewers (so the full-content comparison above is not vacuously "
                      "comparing two empty dicts)", all(k in down_t05 for k in
                      ("p-bench-correctness", "p-bench-standards", "p-bench-both")), down_t05)

    hook_samples = [e for e in manifest.get("entries") or [] if e.get("kind") == "hook_reply"]
    check("[CP5-T05] setup: a hook_reply baseline entry is recorded", bool(hook_samples), manifest.get("entries"))
    if hook_samples:
        try:
            saved_reply = json.loads((BASELINE_DIR / hook_samples[0]["path"]).read_text())
        except (OSError, json.JSONDecodeError) as ex:
            check("[CP5-T05] the saved hook reply baseline parses as JSON", False, str(ex))
            saved_reply = None
        d = Path(_tmp.mkdtemp(prefix="cp5-t05-hook-"))
        r = subprocess.run([sys.executable, str(ENGINE), "init", "hook cap", "--slug", "hk"], cwd=str(d),
                           capture_output=True, text=True, timeout=30)
        check("[CP5-T05] setup: a fresh throwaway project's `init` succeeds", r.returncode == 0,
              (r.returncode, r.stderr))
        if r.returncode == 0 and saved_reply is not None:
            state = str(d / ".ratchet" / "runs" / "hk" / "state.json")
            payload = {"sessionId": "s", "timestamp": 1, "cwd": str(d), "toolName": "edit",
                       "toolArgs": json.dumps({"path": state, "old_str": "a", "new_str": "b"})}
            rg = subprocess.run([sys.executable, str(ENGINE), "hook", "guard", "--host", "copilot"], cwd=str(d),
                                capture_output=True, text=True, timeout=30, input=json.dumps(payload))
            try:
                reply = json.loads(rg.stdout)
            except json.JSONDecodeError as e:
                check("[CP5-T05] a fresh copilot guard reply parses as JSON", False, str(e))
                reply = None
            if reply is not None:
                def _normalize_blocked_path(r_):
                    reason_ = r_.get("permissionDecisionReason", "")
                    return {**r_, "permissionDecisionReason": re.sub(r"Blocked: .*$", "Blocked: <path>", reason_)}
                check("[CP5-T05] the complete fresh guard reply matches CP1's saved baseline exactly, once "
                      "each one's own random temp-dir path is normalized away",
                      _normalize_blocked_path(reply) == _normalize_blocked_path(saved_reply),
                      (saved_reply, reply))

    failures = [e for e in manifest.get("entries") or [] if e.get("kind") == "failure"]
    check("[CP5-T05] setup: at least two failure baseline entries are recorded", len(failures) >= 2, failures)
    for e in failures:
        cmd = e.get("cmd") or []
        try:
            saved = (BASELINE_DIR / e["path"]).read_text()
        except OSError as ex:
            check(f"[CP5-T05] baseline file for failure `{' '.join(cmd)}` is readable", False, str(ex))
            continue
        if cmd and cmd[0] == "no-such-command":
            rf = subprocess.run([sys.executable, str(ENGINE), *cmd], cwd=str(ROOT), capture_output=True,
                                text=True, timeout=30, env=dict(os.environ, CI="1", FORCE_COLOR="0",
                                                                 NO_COLOR="1", PYTHON_COLORS="0"))
            got = rf.stderr
        else:
            empty_dir = Path(_tmp.mkdtemp(prefix="cp5-t05-empty-"))
            rf = subprocess.run([sys.executable, str(ENGINE), "status", "--json"], cwd=str(empty_dir),
                                capture_output=True, text=True, timeout=30)
            got = rf.stderr
        check(f"[CP5-T05] failure case `{' '.join(cmd)}` exits {e.get('exit_code')}, same as CP1's baseline",
              rf.returncode == e.get("exit_code"), rf.returncode)
        check(f"[CP5-T05] failure case `{' '.join(cmd)}`'s stderr is byte-identical to CP1's baseline",
              got == saved, (saved, got))


# ============================================================================================ CP5-T06


def t06_ranking_cost_tier_prefer_for_rotation():
    print("CP5-T06: reviewer ranking and order: cost tier, then prefer_for, then rotation")
    m = cp5y_load_engine()
    env = make_env("claude")
    Path(env["RATCHET_HOME"]).mkdir(parents=True, exist_ok=True)
    bindir = Path(_tmp.mkdtemp(prefix="cp5-t06-bin-"))
    cfg = json.loads(json.dumps(m.DEFAULT_CONFIG))  # a fresh deep copy; this run never mutates the shared literal
    providers = []
    for pid in ("A", "B", "D", "C"):
        script = bindir / f"echo-{pid}.sh"
        script.write_text("#!/bin/sh\necho RATCHET-PROBE-OK\n")
        script.chmod(0o755)
        providers.append({"id": pid, "command": [str(script), "{prompt}"], "company": "acme"})
    providers[0]["prefer_for"] = ["correctness"]        # A: flat, prefers the lens
    providers[3]["cost"] = "per-use"                     # C: per-use, with a recorded OK
    providers[3]["paid_ok_at"] = "2026-01-01T00:00:00Z"
    providers[3]["prefer_for"] = ["correctness"]         # even preferring the lens, tier always loses to flat
    cfg["providers"] = providers
    cfg["review_depth"] = {"normal": ["any-correctness"] * 4}
    cp = {"id": "CP1", "risk": "normal"}

    with engine_env(env):
        health = {}
        at = _dt.datetime.now(_dt.timezone.utc)
        for pid, n in (("A", 2), ("B", 1)):  # D keeps 0 recent starts: fewest starts wins the tie with B
            key = m._health_key(next(p for p in providers if p["id"] == pid))
            health[key] = {"starts": [{"at": (at - _dt.timedelta(minutes=i + 1)).isoformat(timespec="microseconds"),
                                       "id": f"{pid}-s{i}"} for i in range(n)]}
        out = None
        crashed = None
        try:
            out = m.resolve_reviewers(None, cfg, cp, share={"data": health, "ignore": set()})
        except Exception as e:  # noqa: BLE001 - reported by the check below
            crashed = e
    check("[CP5-T06] resolve_reviewers runs with no crash", crashed is None, crashed)
    if crashed is not None:
        return
    got = [r["id"] for r in out]
    check("[CP5-T06] the pick order is A (flat, prefers the lens), D (flat, fewest recent starts), B (flat, "
          "more recent starts), C (per-use, always last, even though it also prefers the lens)",
          got == ["A-correctness", "D-correctness", "B-correctness", "C-correctness"], got)


# ============================================================================================ CP5-T07


def t07_provider_share_window_limit():
    print("CP5-T07: provider_share's window limit blocks an over-share, including two lanes racing to reserve")
    m = cp5y_load_engine()
    env = make_env("claude")
    Path(env["RATCHET_HOME"]).mkdir(parents=True, exist_ok=True)
    bindir = Path(_tmp.mkdtemp(prefix="cp5-t07-bin-"))
    script = bindir / "echo-P.sh"
    script.write_text("#!/bin/sh\necho RATCHET-PROBE-OK\n")
    script.chmod(0o755)
    cfg = json.loads(json.dumps(m.DEFAULT_CONFIG))
    cfg["providers"] = [{"id": "P", "command": [str(script), "{prompt}"], "company": "acme"}]
    cfg["provider_share"] = {"max": 1, "minutes": 15}
    cp = {"id": "CP1", "risk": "normal", "reviewers": ["P-both"]}

    with engine_env(env):
        at = _dt.datetime.now(_dt.timezone.utc)
        p_key = m._health_key(cfg["providers"][0])
        health = {p_key: {"starts": [{"at": at.isoformat(timespec="microseconds"), "id": "already-1"}]}}
        out, crashed = None, None
        try:
            out = m.resolve_reviewers(None, cfg, cp, share={"data": health, "ignore": set()})
        except Exception as e:  # noqa: BLE001 - reported by the check below
            crashed = e
    check("[CP5-T07] setup: resolve_reviewers runs with no crash when the provider is already at its share",
          crashed is None, crashed)
    if crashed is not None:
        return
    got = [r["id"] for r in out]
    check("[CP5-T07] a single round over the window limit is refused the provider, falling back to the host",
          got == ["host-alt-both"], got)
    reasons = out[0].get("reasons", {}) if out else {}
    check("[CP5-T07] the refusal names the exact share limit (1 per 15 minutes, across all lanes)",
          "P-both" in reasons and "1 review start" in reasons["P-both"] and "15 minute" in reasons["P-both"],
          reasons)

    # Real two-lane concurrency: two OS processes race to reserve a start on the SAME provider at the same
    # moment, with no starts recorded yet. Together they must never reserve more than provider_share.max (1).
    cfg_path = Path(_tmp.mkdtemp(prefix="cp5-t07-cfg-")) / "cfg.json"
    cfg2 = dict(cfg)
    cfg2["provider_share"] = {"max": 1, "minutes": 15}
    cfg_path.write_text(json.dumps(cfg2))
    gate = Path(_tmp.mkdtemp(prefix="cp5-t07-gate-"))
    child = Path(_tmp.mkdtemp(prefix="cp5-t07-child-")) / "race.py"
    child.write_text(_CP5T07_CHILD)
    procs = [
        subprocess.Popen([sys.executable, str(child), str(ENGINE), str(cfg_path), f"lane{i}",
                          str(gate / f"ready-{i}"), str(gate / "go")], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
        for i in range(2)
    ]
    ready = cp5v_until(lambda: all((gate / f"ready-{i}").exists() for i in range(2)), timeout=60)
    (gate / "go").write_text("go")
    outs = []
    for p in procs:
        try:
            o, e = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            o, e = p.communicate()
        outs.append((p.returncode, o.strip(), e))
    check("[CP5-T07] setup: both racing lanes ran to completion with no error", ready and
          all(rc == 0 for rc, _o, _e in outs), (ready, [(rc, e[-300:]) for rc, _o, e in outs]))
    picks = [o for _rc, o, _e in outs]
    check("[CP5-T07] exactly one lane got P-both and the other fell back to the host -- never both on P",
          sorted(picks) == ["P-both", "host-alt-both"], picks)
    with engine_env(env):
        final_health = m.provider_health()
        p_key2 = m._health_key(cfg["providers"][0])
    starts_now = len((final_health.get(p_key2) or {}).get("starts") or [])
    check("[CP5-T07] the shared record never ends up with more than provider_share.max (1) starts on P from "
          "this race", starts_now <= 1, (p_key2, final_health))


_CP5T07_CHILD = """
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5t07", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
cfg = json.loads(Path(sys.argv[2]).read_text())
nonce = sys.argv[3]
ready, go = Path(sys.argv[4]), Path(sys.argv[5])
cp = {"id": "CP1", "risk": "normal", "reviewers": ["P-both"]}
def pick(share):
    return m.resolve_reviewers(None, cfg, cp, share=share)
ready.write_text("1")
while not go.exists():
    time.sleep(0.002)
got = m._reserve_under_lock(cfg, (), pick, lambda _picked: [],
                            lambda picked: [(r["id"], nonce) for r in picked if r["id"] == "P-both"])
out = got if got is not None else pick(None)
print(out[0]["id"] if out else "none")
"""


# ============================================================================================ CP5-T08


def t08_probe_shared_claim_and_wait():
    print("CP5-T08: probe_shared: one lane's real test request is reused by a lane asking at the same moment")
    env = make_env("claude")
    Path(env["RATCHET_HOME"]).mkdir(parents=True, exist_ok=True)
    bindir = Path(_tmp.mkdtemp(prefix="cp5-t08-bin-"))
    log_path = Path(_tmp.mkdtemp(prefix="cp5-t08-log-")) / "calls.log"
    sentinel = Path(_tmp.mkdtemp(prefix="cp5-t08-sentinel-")) / "go"
    script = bindir / "slowcli.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"printf 'probe\\n' >> '{log_path}'\n"
        f"while [ ! -f '{sentinel}' ]; do sleep 0.05; done\n"
        "echo RATCHET-PROBE-OK\n")
    script.chmod(0o755)
    cfg_path = Path(_tmp.mkdtemp(prefix="cp5-t08-cfg-")) / "cfg.json"
    cfg_path.write_text(json.dumps({"providers": [{"id": "P", "command": [str(script), "{prompt}"],
                                                    "company": "acme"}]}))
    gate = Path(_tmp.mkdtemp(prefix="cp5-t08-gate-"))
    child = Path(_tmp.mkdtemp(prefix="cp5-t08-child-")) / "shared.py"
    child.write_text(_CP5T08_CHILD)
    roots = [Path(_tmp.mkdtemp(prefix=f"cp5-t08-root{i}-")) for i in range(2)]
    procs = [
        subprocess.Popen([sys.executable, str(child), str(ENGINE), str(cfg_path), str(roots[i]),
                          str(gate / f"ready-{i}"), str(gate / "go")], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
        for i in range(2)
    ]
    ready = cp5v_until(lambda: all((gate / f"ready-{i}").exists() for i in range(2)), timeout=60)
    (gate / "go").write_text("go")
    time.sleep(0.3)  # both lanes are now inside probe_shared; let the first one claim before it is allowed to answer
    sentinel.write_text("go")
    outs = []
    for p in procs:
        try:
            o, e = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            o, e = p.communicate()
        outs.append((p.returncode, o.strip(), e))
    check("[CP5-T08] setup: both lanes ran to completion with no error", ready and
          all(rc == 0 for rc, _o, _e in outs), (ready, [(rc, e[-300:]) for rc, _o, e in outs]))
    results = []
    for _rc, o, _e in outs:
        try:
            results.append(json.loads(o))
        except json.JSONDecodeError:
            results.append(None)
    check("[CP5-T08] both lanes' results parse", all(r is not None for r in results), outs)
    if not all(r is not None for r in results):
        return
    log = log_path.read_text() if log_path.exists() else ""
    check("[CP5-T08] the slow fake CLI is called exactly once (the second lane never sends its own)",
          log.count("probe") == 1, log)
    check("[CP5-T08] both lanes get the same (good, detail)", results[0] == results[1], results)
    check("[CP5-T08] both lanes saw a passing result", results[0].get("good") is True, results)
    with engine_env(env):
        m = cp5y_load_engine()
        health = m.provider_health()
    entry = health.get("opencode:None") or health.get("P") or {}
    # the key is whatever _health_key(pv) computes for a hand-written command provider named "P" with no preset
    key = next(iter(health), None)
    check("[CP5-T08] provider-health.json ends with exactly one recorded result for the provider", key is not None
          and health[key].get("ok") is True, health)
    check("[CP5-T08] no leftover claim is left on the record", not (health.get(key) or {}).get("claim"), health)


_CP5T08_CHILD = """
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("ratchet_cp5t08", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
cfg = json.loads(Path(sys.argv[2]).read_text())
pv = cfg["providers"][0]
ctx = m.Ctx(Path(sys.argv[3]))
ready, go = Path(sys.argv[4]), Path(sys.argv[5])
ready.write_text("1")
while not go.exists():
    time.sleep(0.002)
good, detail = m.probe_shared(ctx, cfg, pv)
print(json.dumps({"good": good, "detail": detail}))
"""


# ============================================================================================ CP5-T09


def t09_quota_bench_own_reset_time_ends_only_by_real_review():
    print("CP5-T09: a quota failure benches the provider for its own stated reset time, and only a real "
          "review ends it")
    m = cp5y_load_engine()
    env = make_env("claude")
    Path(env["RATCHET_HOME"]).mkdir(parents=True, exist_ok=True)
    bindir = Path(_tmp.mkdtemp(prefix="cp5-t09-bin-"))
    marker = bindir / "quota.marker"
    script = bindir / "quotacli.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"if [ -f '{marker}' ]; then\n"
        "  echo 'Rolling usage limit reached. It will reset in 3 hours.' >&2\n"
        "  exit 1\n"
        "fi\n"
        "echo RATCHET-PROBE-OK\n")
    script.chmod(0o755)
    cfg = {"providers": [{"id": "P", "command": [str(script), "{prompt}"], "company": "acme"}],
           "quota_retest_minutes": 20}
    pv = cfg["providers"][0]
    root = Path(_tmp.mkdtemp(prefix="cp5-t09-root-"))

    marker.write_text("x")
    before = _dt.datetime.now(_dt.timezone.utc)
    with engine_env(env):
        ctx = m.Ctx(root)
        good, detail, saved = m.probe_provider(ctx, cfg, pv)
        health = m.provider_health()
    key = next(iter(health), None)
    bench = (health.get(key) or {}).get("bench") if key else None
    check("[CP5-T09] the failing test request is recorded", not good and key is not None, (good, detail, health))
    check("[CP5-T09] it is benched with quota=True", isinstance(bench, dict) and bench.get("quota") is True,
          bench)
    if not (isinstance(bench, dict) and bench.get("quota")):
        return
    until = _dt.datetime.fromisoformat(str(bench["until"]).replace("Z", "+00:00"))
    expected = before + _dt.timedelta(hours=3)
    check("[CP5-T09] the bench runs until roughly the time the provider itself named (3 hours), not the "
          "quota_retest_minutes default", abs((until - expected).total_seconds()) < 120, (until, expected))

    marker.unlink()  # the provider now answers again, as if the CLI itself recovered early
    with engine_env(env):
        good2, detail2, _saved2 = m.probe_provider(ctx, cfg, pv)
        health2 = m.provider_health()
    bench2 = (health2.get(key) or {}).get("bench")
    check("[CP5-T09] a later passing test request alone does NOT end the quota bench", good2 and isinstance(
          bench2, dict) and bench2.get("quota") is True, (good2, detail2, bench2))

    sent_after_bench = before + _dt.timedelta(minutes=1)
    with engine_env(env):
        ended = m.review_succeeded(cfg, "P", sent_after_bench, ctx=None)
        health3 = m.provider_health()
    bench3 = (health3.get(key) or {}).get("bench")
    check("[CP5-T09] review_succeeded (a real review sent after the bench began, that succeeded) reports True "
          "and ends the quota bench", ended and not bench3, (ended, bench3))


# ============================================================================================ CP5-T10


def t10_record_round_failure_once_and_leaves_moved_on_round_alone():
    print("CP5-T10: record_round_failure records a round's failure once, and leaves a moved-on round alone")
    from trust_helpers import cp3_project

    env = make_env("claude")
    Path(env["RATCHET_HOME"]).mkdir(parents=True, exist_ok=True)
    m = cp5y_load_engine()
    d = cp3_project(env)
    with engine_env(env):
        ctx = m.Ctx(Path(d))
        cfg = ctx.config()
        st = ctx.state()
        st["checkpoints"]["CP1"]["adv_round"] = {"round": 1, "reviewers": ["codex-both"], "nonce": "n1",
                                                  "failed_providers": [], "failed_reviewers": []}
        ctx.save_state(st)

        ok1, crashed1 = None, None
        try:
            ok1 = m.record_round_failure(ctx, cfg, "CP1", 1, ["codex-both"], own="codex-both")
        except Exception as e:  # noqa: BLE001 - reported by the check below
            crashed1 = e
        st_after1 = ctx.state()
    check("[CP5-T10] record_round_failure for the round it's really in runs with no crash", crashed1 is None,
          crashed1)
    check("[CP5-T10] it returns True (recorded)", ok1 is True, ok1)
    rnd1 = st_after1["checkpoints"]["CP1"].get("adv_round") or {}
    check("[CP5-T10] the provider behind codex-both is recorded in failed_providers",
          rnd1.get("failed_providers") == ["codex"], rnd1)
    check("[CP5-T10] codex-both itself is recorded in failed_reviewers (own)",
          rnd1.get("failed_reviewers") == ["codex-both"], rnd1)

    with engine_env(env):
        st2 = ctx.state()
        st2["checkpoints"]["CP1"]["adv_round"]["round"] = 2
        st2["checkpoints"]["CP1"]["adv_round"]["failed_providers"] = []
        st2["checkpoints"]["CP1"]["adv_round"]["failed_reviewers"] = []
        ctx.save_state(st2)
        before_stale = ctx.state()["checkpoints"]["CP1"]["adv_round"]

        ok2, crashed2 = None, None
        try:
            ok2 = m.record_round_failure(ctx, cfg, "CP1", 1, ["codex-both"], own="codex-both")
        except Exception as e:  # noqa: BLE001 - reported by the check below
            crashed2 = e
        after_stale = ctx.state()["checkpoints"]["CP1"]["adv_round"]
    check("[CP5-T10] a call for a round that has already moved on runs with no crash", crashed2 is None,
          crashed2)
    check("[CP5-T10] it still reports success (True) even though it changed nothing", ok2 is True, ok2)
    check("[CP5-T10] the now-current round (round 2) is left exactly as it was: the stale call recorded "
          "nothing against it", after_stale == before_stale, (before_stale, after_stale))


# ============================================================================================ CP5-T11
# Reuses (never reinvents) tests/test_trust_cp5_e.py's own cp5r13_shared_write_violations scan, feeding it
# provider_discovery.py's real and probed source directly -- the same pattern test_modularize_cp4.py's
# CP4-15 already established for records.py.


def _import_cp5_e():
    try:
        import test_trust_cp5_e as tt
        return tt, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def t11_shared_write_scan_covers_provider_discovery():
    print("CP5-T11: the lock-bypassing direct-write scan also catches a bypass placed inside "
          "provider_discovery.py")
    check("[CP5-T11] setup: provider_discovery.py exists", PROVIDER_DISCOVERY.exists())
    if not PROVIDER_DISCOVERY.exists():
        return
    tt, err = _import_cp5_e()
    check("[CP5-T11] setup: tests/test_trust_cp5_e.py imports cleanly so its shared-write scan can be reused, "
          "never reinvented", tt is not None, err)
    if tt is None:
        return
    combined = live_ratchet_source() + "\n" + PROVIDER_DISCOVERY.read_text()
    bad = tt.cp5r13_shared_write_violations(combined)
    check("[CP5-T11] the real, unmodified provider_discovery.py plus ratchet.py source together produce zero "
          "shared-write violations", bad == [], bad[:10])
    probe = (PROVIDER_DISCOVERY.read_text() + "\n\n"
             "def _cp5_t11_probe_stray(path):\n    _write_record(_health_path(), {})\n\n"
             "def _cp5_t11_probe_fine(path):\n    update_record(_health_path(), lambda d: d)\n")
    flagged = sorted({fn for fn, _l, _w in tt.cp5r13_shared_write_violations(probe)})
    check("[CP5-T11] a hand-written bypass write planted inside provider_discovery.py's own source (not "
          "through update_record) is flagged by the scan, and a legitimate update_record-based write next "
          "to it is not", flagged == ["_cp5_t11_probe_stray"], flagged)
    # Deliberately does NOT also re-run tests/test_quota_shared.py, tests/test_discover_paid.py,
    # tests/test_discover_omp.py, tests/test_providers.py or tests/test_quota_share.py here: this
    # checkpoint's own tests.command already runs all five of those suites separately through the enclosing
    # runner (tests/run_all.py), and test_quota_shared.py alone is slow enough (~990s) to risk exceeding
    # run_all.py's outer per-suite watchdog for THIS file if nested inside it too. See .ratchet/learnings.md
    # (2026-10-20 director steer for CP3..CP9): never nest a full re-run of an already-covered heavy suite
    # inside a checkpoint's own test file.


# ============================================================================================ CP5-T12


def t12_full_suite_passes():
    print("CP5-T12: the complete local suite passes after this checkpoint")
    # See .ratchet/learnings.md's final resolution (2026-10-20) for the regression.log pattern: a checkpoint's
    # own "full suite passes" case cannot tell a fresh result of the run it is currently part of apart from a
    # stale log left by an earlier, unrelated attempt, so it does not read evidence/CP5/regression.log at all
    # by default, and does not nest another full run of tests/run_all.py either -- this is already
    # structurally enforced by `ratchet.py complete`, which refuses to mark a checkpoint done when its own
    # regression run fails. Set RATCHET_CP5_T12_FULL_SUITE=1 to actually pay for running it inline.
    if os.environ.get("RATCHET_CP5_T12_FULL_SUITE") == "1":
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=5400)
        m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
        failed = int(m.group(2)) if m else -1
        check("[CP5-T12] python3 tests/run_all.py exits 0 with 0 failed", r.returncode == 0 and failed == 0,
              (r.returncode, r.stdout[-400:]))
        return
    check("[CP5-T12] done-means #5 (full suite passes) is enforced structurally by `ratchet.py complete`, "
          "which refuses to mark a checkpoint done when its own regression run fails -- not by this test "
          "case re-reading complete's own evidence file; set RATCHET_CP5_T12_FULL_SUITE=1 to actually run the "
          "full suite inline instead", True)


# ============================================================================================ CP5-T13
# DIRECTOR-APPROVED SCOPE ADDITION (added while gating CP5): tests/run_all.py passes the SAME
# RATCHET_TEST_REPORT path unchanged to every suite subprocess it runs (run_all.py's Runner.run() does
# `env = dict(os.environ, TMPDIR=..., ...)`, which never touches RATCHET_TEST_REPORT), and every
# report-writing suite does a destructive `open(path, "w")` of its own -- so whichever suite finishes last
# completely destroys every other suite's results. This is exactly what happened for real to CP5's own red
# run: test_modularize_cp5.py's 12 CP5-tagged cases were destroyed by tests/test_quota_share.py's unrelated
# CP6-tagged ones, because CP5's own tests.command
# (`python3 tests/run_all.py tests/test_modularize_cp5.py ... tests/test_quota_share.py`) runs several
# report-writing suites together against one shared $RATCHET_TEST_REPORT.
#
# This case proves both directions with two tiny, throwaway, standalone report-writing scripts (never part
# of the real suite; they live in their own throwaway temp folder, not under tests/, and are never
# discovered by a plain `tests/run_all.py` glob run) that write distinct, made-up case ids (ZZ-A01/ZZ-B01)
# on exit, the same write-report-at-exit pattern every real suite in this folder already uses:
#   1. Against the PINNED pre-fix tests/run_all.py (git show of commit 9b9dfeb, confirmed byte-identical to
#      this run's own working-tree copy right now, before any CP5 fix lands): whichever of the two throwaway
#      suites finishes LAST always ends up as the ONLY case in the final report -- the bug, reproduced, in
#      both write-order directions.
#   2. Against the LIVE (to-be-fixed) tests/run_all.py in this working tree: both throwaway suites' cases
#      must appear in the final report, in both write-order directions. This is the part that is expected to
#      be RED right now (the live copy is byte-identical to the pinned one, so it has the exact same bug),
#      and will go green once tests/run_all.py isolates each suite's report and merges them afterward.
#   3. A real, pre-existing, fast, single-report-writing suite (tests/test_digest_docstring.py, run alone,
#      the same "one suite in the command" shape CP4's own tests.command uses) produces byte-identical
#      report content run through the pinned pre-fix copy and run through the live copy -- proving the fix
#      does not change anything for a gate whose tests.command runs only one report-writing suite.
PINNED_RUN_ALL_COMMIT = "9b9dfeb6e0994db2975ce5803420b3cf4bcc9882"  # last commit before this run's own fix
PINNED_RUN_ALL_RELPATH = "tests/run_all.py"
LIVE_RUN_ALL = ROOT / "tests" / "run_all.py"
SINGLE_SUITE_REPRESENTATIVE = ROOT / "tests" / "test_digest_docstring.py"  # fast (~1s), real, report-writing

_ZZ_A_SRC = (
    "import json, os, sys, time\n"
    "time.sleep(float(os.environ.get('ZZ_A_DELAY', '0')))\n"
    "path = os.environ.get('RATCHET_TEST_REPORT')\n"
    "if path:\n"
    "    with open(path, 'w') as f:\n"
    "        json.dump({'cases': [{'id': 'ZZ-A01', 'status': 'passed', 'message': ''}]}, f)\n"
    "print('ZZ-A01 wrote its report')\n"
    "sys.exit(0)\n"
)
_ZZ_B_SRC = (
    "import json, os, sys, time\n"
    "time.sleep(float(os.environ.get('ZZ_B_DELAY', '0')))\n"
    "path = os.environ.get('RATCHET_TEST_REPORT')\n"
    "if path:\n"
    "    with open(path, 'w') as f:\n"
    "        json.dump({'cases': [{'id': 'ZZ-B01', 'status': 'passed', 'message': ''}]}, f)\n"
    "print('ZZ-B01 wrote its report')\n"
    "sys.exit(0)\n"
)


def _cp5t13_pinned_run_all_script():
    src = show_at(PINNED_RUN_ALL_COMMIT, PINNED_RUN_ALL_RELPATH)
    p = Path(_tmp.mkdtemp(prefix="cp5-t13-pinned-")) / "run_all_pinned.py"
    p.write_text(src)
    return p


def _cp5t13_run_pair(run_all_script, suite_a, suite_b, delay_a, delay_b, report_path):
    """Runs `suite_a`/`suite_b` together through `run_all_script`, with a fresh, isolated timings cache (so
    this never reads or writes the real user cache) and a shared $RATCHET_TEST_REPORT at `report_path`.
    Returns (CompletedProcess, parsed report dict or None)."""
    cache_dir = Path(_tmp.mkdtemp(prefix="cp5-t13-cache-"))
    env = dict(os.environ)
    for k in ("RATCHET_RUN_ALL", "RATCHET_RUN_ALL_TAG", "RATCHET_RUN_ALL_SUITES"):
        env.pop(k, None)
    env["RATCHET_TEST_REPORT"] = str(report_path)
    env["ZZ_A_DELAY"] = str(delay_a)
    env["ZZ_B_DELAY"] = str(delay_b)
    env["XDG_CACHE_HOME"] = str(cache_dir)
    if report_path.exists():
        report_path.unlink()
    r = subprocess.run([sys.executable, str(run_all_script), "--workers", "2", "--timeout", "30",
                        str(suite_a), str(suite_b)], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=90, env=env)
    report = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text())
        except json.JSONDecodeError:
            report = None
    return r, report


def _cp5t13_ids(report):
    return {c.get("id") for c in (report or {}).get("cases", [])}


def t13_run_all_never_lets_one_suite_clobber_another():
    print("CP5-T13: run_all.py never lets one report-writing suite's results clobber another's")
    suites_dir = Path(_tmp.mkdtemp(prefix="cp5-t13-suites-"))
    suite_a = suites_dir / "test_zz_a_cp5t13.py"
    suite_b = suites_dir / "test_zz_b_cp5t13.py"
    suite_a.write_text(_ZZ_A_SRC)
    suite_b.write_text(_ZZ_B_SRC)
    DELAY_FAST, DELAY_SLOW = "0", "1.5"

    try:
        pinned_src = show_at(PINNED_RUN_ALL_COMMIT, PINNED_RUN_ALL_RELPATH)
    except Exception as e:
        check("[CP5-T13] setup: the pinned pre-fix tests/run_all.py source is readable via git show", False,
              str(e))
        return
    live_src = LIVE_RUN_ALL.read_text()
    # Informational note only (not a pass/fail check): whether the live tests/run_all.py still matches
    # the pre-fix 9b9dfeb pin is expected to flip to False once the real fix lands -- that's the whole
    # point of Part 2 below, so it is not an invariant this case asserts on.
    print("CP5-T13: live run_all.py byte-identical to pinned pre-fix 9b9dfeb copy right now: %s"
          % (pinned_src == live_src))
    pinned_script = _cp5t13_pinned_run_all_script()

    # ---- Part 1: the bug, reproduced against the PINNED pre-fix run_all.py, both directions ----------------
    report_path_1 = Path(_tmp.mkdtemp(prefix="cp5-t13-report-")) / "report.json"
    r1, rep1 = _cp5t13_run_pair(pinned_script, suite_a, suite_b, DELAY_FAST, DELAY_SLOW, report_path_1)
    check("[CP5-T13] setup: the pinned pre-fix run_all.py runs both throwaway suites to completion (direction "
          "1: A fast, B slow)", r1.returncode == 0, (r1.returncode, r1.stdout[-500:], r1.stderr[-500:]))
    check("[CP5-T13] bug reproduced on the pinned pre-fix run_all.py: when B finishes last, the final report "
          "ends with ONLY ZZ-B01 -- ZZ-A01's result is destroyed", _cp5t13_ids(rep1) == {"ZZ-B01"},
          (rep1, r1.stdout[-300:]))

    report_path_2 = Path(_tmp.mkdtemp(prefix="cp5-t13-report-")) / "report.json"
    r2, rep2 = _cp5t13_run_pair(pinned_script, suite_a, suite_b, DELAY_SLOW, DELAY_FAST, report_path_2)
    check("[CP5-T13] setup: the pinned pre-fix run_all.py runs both throwaway suites to completion (direction "
          "2: A slow, B fast)", r2.returncode == 0, (r2.returncode, r2.stdout[-500:], r2.stderr[-500:]))
    check("[CP5-T13] bug reproduced on the pinned pre-fix run_all.py: when A finishes last, the final report "
          "ends with ONLY ZZ-A01 -- ZZ-B01's result is destroyed", _cp5t13_ids(rep2) == {"ZZ-A01"},
          (rep2, r2.stdout[-300:]))

    # ---- Part 2: the fix, proven against the LIVE (to-be-fixed) run_all.py, both directions ----------------
    report_path_3 = Path(_tmp.mkdtemp(prefix="cp5-t13-report-")) / "report.json"
    r3, rep3 = _cp5t13_run_pair(LIVE_RUN_ALL, suite_a, suite_b, DELAY_FAST, DELAY_SLOW, report_path_3)
    check("[CP5-T13] setup: the live run_all.py runs both throwaway suites to completion (direction 1: A "
          "fast, B slow)", r3.returncode == 0, (r3.returncode, r3.stdout[-500:], r3.stderr[-500:]))
    check("[CP5-T13] the live run_all.py never lets one report-writing suite clobber another's results: "
          "direction 1 (B finishes last) still has BOTH ZZ-A01 and ZZ-B01 in the final report",
          _cp5t13_ids(rep3) == {"ZZ-A01", "ZZ-B01"}, (rep3, r3.stdout[-300:]))

    report_path_4 = Path(_tmp.mkdtemp(prefix="cp5-t13-report-")) / "report.json"
    r4, rep4 = _cp5t13_run_pair(LIVE_RUN_ALL, suite_a, suite_b, DELAY_SLOW, DELAY_FAST, report_path_4)
    check("[CP5-T13] setup: the live run_all.py runs both throwaway suites to completion (direction 2: A "
          "slow, B fast)", r4.returncode == 0, (r4.returncode, r4.stdout[-500:], r4.stderr[-500:]))
    check("[CP5-T13] the live run_all.py never lets one report-writing suite clobber another's results: "
          "direction 2 (A finishes last) still has BOTH ZZ-A01 and ZZ-B01 in the final report",
          _cp5t13_ids(rep4) == {"ZZ-A01", "ZZ-B01"}, (rep4, r4.stdout[-300:]))

    # ---- Part 3: a real, pre-existing, single-report-writing tests.command is unaffected by the fix --------
    report_path_5p = Path(_tmp.mkdtemp(prefix="cp5-t13-report-")) / "report.json"
    cache_dir_single_pinned = Path(_tmp.mkdtemp(prefix="cp5-t13-cache-single-"))
    env_single_pinned = dict(os.environ, RATCHET_TEST_REPORT=str(report_path_5p),
                              XDG_CACHE_HOME=str(cache_dir_single_pinned))
    for k in ("RATCHET_RUN_ALL", "RATCHET_RUN_ALL_TAG", "RATCHET_RUN_ALL_SUITES"):
        env_single_pinned.pop(k, None)
    r5p = subprocess.run([sys.executable, str(pinned_script), "--workers", "2", "--timeout", "60",
                          str(SINGLE_SUITE_REPRESENTATIVE)], cwd=str(ROOT), capture_output=True, text=True,
                         timeout=90, env=env_single_pinned)
    check("[CP5-T13] setup: the pinned pre-fix run_all.py runs the single real report-writing suite "
          "(tests/test_digest_docstring.py) to completion", r5p.returncode == 0,
          (r5p.returncode, r5p.stdout[-500:], r5p.stderr[-500:]))
    pinned_single_text = report_path_5p.read_text() if report_path_5p.exists() else None

    report_path_5l = Path(_tmp.mkdtemp(prefix="cp5-t13-report-")) / "report.json"
    cache_dir_single_live = Path(_tmp.mkdtemp(prefix="cp5-t13-cache-single-"))
    env_single_live = dict(os.environ, RATCHET_TEST_REPORT=str(report_path_5l),
                            XDG_CACHE_HOME=str(cache_dir_single_live))
    for k in ("RATCHET_RUN_ALL", "RATCHET_RUN_ALL_TAG", "RATCHET_RUN_ALL_SUITES"):
        env_single_live.pop(k, None)
    r5l = subprocess.run([sys.executable, str(LIVE_RUN_ALL), "--workers", "2", "--timeout", "60",
                         str(SINGLE_SUITE_REPRESENTATIVE)], cwd=str(ROOT), capture_output=True, text=True,
                        timeout=90, env=env_single_live)
    check("[CP5-T13] setup: the live run_all.py runs the single real report-writing suite "
          "(tests/test_digest_docstring.py) to completion", r5l.returncode == 0,
          (r5l.returncode, r5l.stdout[-500:], r5l.stderr[-500:]))
    live_single_text = report_path_5l.read_text() if report_path_5l.exists() else None

    check("[CP5-T13] a pre-existing single-report-writing tests.command (tests/test_digest_docstring.py "
          "alone, the same shape CP4's own tests.command uses) produces byte-identical report content "
          "before and after the fix", pinned_single_text is not None and
          pinned_single_text == live_single_text, (pinned_single_text, live_single_text))


TESTS = [
    ("CP5-T01", t01_module_byte_identical),
    ("CP5-T02", t02_explicit_imports_no_star_no_cycle),
    ("CP5-T03", t03_no_new_global_mutable_registry),
    ("CP5-T04", t04_providers_output_unchanged),
    ("CP5-T05", t05_help_status_hook_exit_codes_unchanged),
    ("CP5-T06", t06_ranking_cost_tier_prefer_for_rotation),
    ("CP5-T07", t07_provider_share_window_limit),
    ("CP5-T08", t08_probe_shared_claim_and_wait),
    ("CP5-T09", t09_quota_bench_own_reset_time_ends_only_by_real_review),
    ("CP5-T10", t10_record_round_failure_once_and_leaves_moved_on_round_alone),
    ("CP5-T11", t11_shared_write_scan_covers_provider_discovery),
    ("CP5-T12", t12_full_suite_passes),
    ("CP5-T13", t13_run_all_never_lets_one_suite_clobber_another),
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
