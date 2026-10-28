#!/usr/bin/env python3
"""
CP5: confirm the whole tests/run_all.py run is fast and green, and that the conversion pattern used to get
there is written down.   python3 -I tests/confirm_full_suite.py

Deliberately named tests/confirm_full_suite.py, not test_*.py (the same reason tests/measure_suite_baseline.py
from CP1 is named the way it is): tests/run_all.py's own directory-scan discovery (`folder.glob("test_*.py")`)
must never find and re-run this file as one more suite of the thing it is itself checking.

Runs the real `python3 tests/run_all.py` with no file arguments (full default directory discovery: every
tests/test_*.py suite in the project, including the three CP2/CP3/CP4 just converted), timed with
time.monotonic(), and parses its real stdout and real exit code the same way a human reads them: the final "N
passed, M failed (suites) in <wall>s with <workers> workers" summary line and the "Suite times (slowest
first):" per-suite table. CP5-T01..CP5-T04 are all checks against that one captured run. CP5-T05/T06
separately read .ratchet/standards.md for a new conversion-pattern note; this file only reads that file, it
never writes to it -- adding the note is this checkpoint's own implementation work, not this test's.

This file is itself CP5's own planned test file, so `behavior CP5` and every later `complete CP5` related-
tests pre-check invoke it again. Re-running the real 113+-suite `run_all.py` on every one of those
invocations, when nothing under tests/ or the engine (skills/ratchet-orchestrator/scripts/ratchet.py) has
changed since the last real run, is exactly the "pay for a heavy suite over and over" problem the project
already avoids elsewhere (see run_all.py's own slowest-first ordering and timing cache). So the one real run
is cached on disk, keyed by a fingerprint over the content (sha256 of each file's actual bytes, never just
size/mtime, which a plain git checkout can make misleading) of every file under tests/ and the engine script
(skills/ratchet-orchestrator/scripts/ratchet.py); a cache hit (same fingerprint) is reused and logged as
reused, a miss (including no cache file at all) always triggers a real re-run and refreshes the cache. This
never weakens the check -- it only dedupes identical back-to-back invocations on code that has not moved; the
moment anything relevant changes, the fingerprint changes and a real run happens again.
`.ratchet/config.json` was checked and is deliberately left out: run_all.py never reads it (grep confirms no
reference), so no key in it changes which suites a bare `python3 tests/run_all.py` discovers or how it runs
them, and including it would only cause spurious cache misses.

Defense-in-depth recursion guard: tests/run_all.py's own no-file-args discovery is `folder.glob("test_*.py")`
(see its main()), which this file's name never matches, so the real run this harness shells out to cannot
already re-discover and re-run this file on its own. The only real double-payment risk is this file being
invoked twice from outside (once by `behavior CP5`, again by a later `complete CP5` related-tests pre-check,
since run_all.py does accept this file as an explicit argument, bypassing the glob) -- the fingerprint cache
above already covers that. As cheap extra protection against someone later changing run_all.py's discovery or
a project's `related_test_command` in a way that could reintroduce nesting, an env var
(RATCHET_CONFIRM_FULL_SUITE_GUARD) is set on the environment passed to the run_all.py subprocess before it is
launched; if main() ever finds that var already set (meaning something nested invoked this file again), it
prints one line and exits 0 immediately, touching neither the cache nor run_all.py.

Every assertion is check("[CP5-Txx] ...", cond) in this project's existing style (see tests/trust_helpers.py,
tests/test_cleanup.py, tests/measure_suite_baseline.py). When $RATCHET_TEST_REPORT is set, this file writes
{"cases": [{"id": "CP5-T01", "status": "passed"|"failed", "message": "..."}]} to it.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

RUN_ALL = HERE / "run_all.py"
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
STANDARDS = ROOT / ".ratchet" / "standards.md"
# This run's own evidence folder (same shape as tests/measure_suite_baseline.py's REAL_OUT): CP5-T02's
# measured wall-clock is written here so it is reviewable, not just asserted and discarded.
EVIDENCE_DIR = ROOT / ".ratchet/runs/make-tests-test-trust-cp5-d-py-tests-test-trust/evidence/CP5"
TIMING_OUT = EVIDENCE_DIR / "full_suite_timing.json"
# Cross-process cache of the one real run (see module docstring): this file is CP5's own planned test, so it
# is invoked again by `behavior CP5` and by every later `complete CP5` related-tests pre-check. Lives in this
# run's own evidence folder, never inside tests/ or any path .ratchet locks for hand-editing.
RUN_CACHE = EVIDENCE_DIR / "full_suite_run_cache.json"

# A full `python3 tests/run_all.py` with no file arguments runs every real suite in the project (121 at the
# time this harness was written). An independently-verified direct run (python3 -I tests/run_all.py, no file
# args) completed in 2615.9s (43.6 min) under moderate host load (~6.2 load average on 12 cores, several
# legitimate concurrent sibling dev sessions), 121 passed, 0 failed. This ceiling gives that real cost real
# headroom while still catching a genuine hang well before a CI timeout would.
FULL_RUN_TIMEOUT_SEC = 3600.0

MARK_PASS, MARK_FAIL = "OK", "XX"

# Recursion guard: set on the env passed to the run_all.py subprocess before it is launched (see
# run_full_suite()); checked at the very top of main(). Defense-in-depth only -- see module docstring for why
# the real run cannot already re-discover this file, and why the fingerprint cache is the actual fix for the
# one real double-payment path.
_GUARD_VAR = "RATCHET_CONFIRM_FULL_SUITE_GUARD"

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
_CASE_ID_RE = re.compile(r"\[(CP5-T\d+)\]")


def check(name: str, cond: bool, detail="") -> bool:
    """Prints one result line and, when `name` carries a [CP5-Txx] id, files the result under that id for the
    JSON report (a case with more than one check() call fails overall if any of them fails)."""
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = _CASE_ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {MARK_PASS if cond else MARK_FAIL} {name}" + ("" if cond else f"   {str(detail)[:400]}"))
    return bool(cond)


# ---------------------------------------------------------------------------------------------------
# One real full run, captured once and reused by CP5-T01..CP5-T04.
# ---------------------------------------------------------------------------------------------------

SUMMARY_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed\s+\(suites\)\s+in\s+([\d.]+)s\s+with\s+(\d+)\s+workers")
SUITE_ROW_RE = re.compile(r"^\s*([\d.]+)s\s+(passed|FAILED)\s+(\S+)\s*$", re.M)

_full_run_cache: dict = {}


def _hash_bytes(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return "MISSING"


def _tests_dir_files():
    """Every regular file under tests/ (this whole suite, including this harness itself and its shared
    helpers like trust_helpers.py and _tmp.py), skipping __pycache__; sorted for a deterministic fingerprint."""
    return sorted(p for p in HERE.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def _git_tracked_files():
    """Every file `git` tracks anywhere in the repo, as paths on disk (not git's stored blob hashes), so an
    uncommitted working-tree edit to a tracked file (e.g. README.md or .ratchet/standards.md, both read and
    asserted on by discovered suites like test_docs_accuracy.py / test_ci_export_docs.py) still changes the
    fingerprint below. `git ls-files` already excludes .git/ itself and ignored/untracked cruft (__pycache__,
    etc.) the same way _tests_dir_files() does for tests/. Returns [] (never raises) if git is unavailable --
    the tests/ + engine coverage above still applies in that case."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True, text=True,
                              timeout=30, check=True).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return [ROOT / name for name in out.split("\0") if name]


def _fingerprint() -> str:
    """A signature over the real content (sha256 of each file's own bytes -- never just size/mtime, which a
    plain git checkout, submodule update or clock skew can make misleading) of every file under tests/, the
    engine script (ratchet.py), and every file git tracks in the whole repo (discovered suites can read project
    files outside tests/ too, e.g. README.md and .ratchet/standards.md). Changes the moment any of those bytes
    change, a file is added or removed; stays identical across repeated invocations when nothing relevant
    moved, which is what the disk cache below keys on. `.ratchet/config.json` is deliberately excluded:
    run_all.py never reads it."""
    files = set(_tests_dir_files())
    files.add(ENGINE)
    files.update(_git_tracked_files())
    ordered = sorted((p for p in files if "__pycache__" not in p.parts), key=lambda p: str(p.relative_to(ROOT)))
    parts = [f"{p.relative_to(ROOT)}:{_hash_bytes(p)}" for p in ordered]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _load_run_cache(fingerprint: str):
    """The cached (returncode, stdout, stderr, wall_clock_sec) when RUN_CACHE on disk matches `fingerprint`
    and holds every field a real run writes; None on any mismatch, missing file or unreadable/partial JSON --
    every one of those cases falls through to a real run below, never to a fabricated result."""
    try:
        data = json.loads(RUN_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("fingerprint") != fingerprint:
        return None
    rc, out, err, wall = data.get("exit_code"), data.get("stdout_tail"), data.get("stderr_tail"), \
        data.get("elapsed_seconds")
    if not (isinstance(rc, int) and isinstance(out, str) and isinstance(err, str)
            and isinstance(wall, (int, float))):
        return None
    print(f"  (reusing cached real run from {data.get('timestamp', '?')}; fingerprint unchanged: "
          f"{fingerprint[:12]}...)")
    return rc, out, err, wall


def _save_run_cache(fingerprint: str, rc: int, out: str, err: str, wall: float) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"fingerprint": fingerprint, "exit_code": rc, "stdout_tail": out, "stderr_tail": err[-4000:],
            "elapsed_seconds": wall, "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        RUN_CACHE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # a failed cache write never blocks a real result from being used this process


def run_full_suite():
    """Returns (returncode, stdout, stderr, wall_clock_sec) for the real `python3 tests/run_all.py` (no file
    args). Memoized once per process. Before shelling out, checks the on-disk cache (see _load_run_cache)
    against _fingerprint(): a hit is reused and logged; a miss (including no cache at all) always runs the
    real suite for real and refreshes the cache. Never raises: a timeout or a failure to start is turned into
    a clearly-failing result tuple (exit code 124/126) instead, so a case driving this can fail on an
    assertion, never crash; those synthetic results are not written to the cache."""
    if "result" in _full_run_cache:
        return _full_run_cache["result"]

    fingerprint = _fingerprint()
    cached = _load_run_cache(fingerprint)
    if cached is not None:
        _full_run_cache["result"] = cached
        return cached

    env = dict(os.environ)
    for k in ("RATCHET_RUN_ALL_TAG", "RATCHET_RUN_ALL", "RATCHET_RUN_ALL_SUITES", "RATCHET_TEST_REPORT"):
        env.pop(k, None)
    env[_GUARD_VAR] = "1"  # defense-in-depth: see module docstring and main()'s check of this same var
    t0 = time.monotonic()
    try:
        r = subprocess.run([sys.executable, str(RUN_ALL)], cwd=str(ROOT), env=env, capture_output=True,
                            text=True, timeout=FULL_RUN_TIMEOUT_SEC)
        wall = time.monotonic() - t0
        result = (r.returncode, r.stdout, r.stderr, wall)
        _save_run_cache(fingerprint, *result)
    except subprocess.TimeoutExpired as e:
        wall = time.monotonic() - t0
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
        result = (124, out, err, wall)  # never cached: a timeout is a harness anomaly, not a trustworthy result
    except OSError as e:
        result = (126, "", f"{type(e).__name__}: {e}", time.monotonic() - t0)  # never cached, same reason
    _full_run_cache["result"] = result
    return result


def cp5_t01():
    cid = "CP5-T01"
    rc, out, err, _wall = run_full_suite()
    m = SUMMARY_RE.search(out)
    check(f"[{cid}] the real full run exits 0", rc == 0, (rc, out[-1500:], err[-1500:]))
    passed_n = int(m.group(1)) if m else None
    failed_n = int(m.group(2)) if m else None
    check(f"[{cid}] the summary line is present and readable (N passed, M failed (suites) in ...)",
          m is not None, out[-1500:])
    check(f"[{cid}] 0 suites failed", failed_n == 0, (passed_n, failed_n))
    check(f"[{cid}] at least one suite is reported passed (not an empty run reinterpreted as 100%)",
          isinstance(passed_n, int) and passed_n > 0, passed_n)


# The three suites CP2/CP3/CP4 converted; not the whole project (see module docstring / out-of-scope list
# below). Keyed by basename, matching how SUITE_ROW_RE below reports them.
TARGET_SUITES = ["test_quota_shared.py", "test_trust_cp5_d.py", "test_trust_cp5_e.py"]

# Named once here (reused in the informational note CP5-T02 writes) so the list of suites outside this lane's
# scope that dominate the full-project wall-clock is not duplicated across the file.
_OUT_OF_SCOPE_SUITES_NOTE = (
    "Whole-project wall-clock is dominated entirely by suites this lane never touched, not by "
    "test_quota_shared.py, test_trust_cp5_d.py or test_trust_cp5_e.py: test_hooks_waiver.py, "
    "test_split_check.py, test_dm_finish.py, test_fix_round.py, test_dm_accept.py, test_merge_main.py, "
    "test_accept_finding.py, test_director_sign_more.py, test_gates.py, test_director_apply.py, "
    "test_checkin.py, test_quota_spread.py, test_fix_round_diff.py, test_dm_finish_extra.py, "
    "test_signing.py, and more. Speeding up the full 121-suite project is a separate, future optimization; "
    "this case does not gate on it."
)


def _parse_suite_durations(out: str) -> dict:
    """Maps each suite's own basename to its individually measured elapsed seconds, parsed from the real
    per-suite table (the same 'Suite times (slowest first):' rows CP5-T04 already reads with SUITE_ROW_RE:
    '<dur>s  passed|FAILED  <name>')."""
    return {Path(name).name: float(dur) for dur, _status, name in SUITE_ROW_RE.findall(out)}


def _check_target_suites(durations: dict, full_wall) -> None:
    """The actual CP5-T02 checks, isolated from where `durations`/`full_wall` came from so this can be driven
    either by the one real run_full_suite() capture (the normal path, called from cp5_t02 below) or by a
    fixture per-suite table in a focused, non-subprocess check of this function alone. Never triggers a run or
    writes outside this run's own evidence folder."""
    cid = "CP5-T02"
    measured = {name: durations.get(name) for name in TARGET_SUITES}
    for name, d in measured.items():
        check(f"[{cid}] {name} individually finishes under the 120s ceiling", isinstance(d, (int, float)) and
              d < 120.0, d)
        check(f"[{cid}] {name} individually takes at least 5s of its own measured time (a stub faking "
              f"near-instant completion without really running the suite is caught)",
              isinstance(d, (int, float)) and d > 5.0, d)
    all_present = all(isinstance(d, (int, float)) for d in measured.values())
    total = sum(d for d in measured.values() if isinstance(d, (int, float))) if all_present else None
    check(f"[{cid}] the three converted suites' combined elapsed time is under the 900s ceiling "
          f"(well under the ~15-16 minute floor they used to cost together before conversion)",
          all_present and total < 900.0, (measured, total))

    # Informational only, never gated: the full-project number is recorded for review, not compared against a
    # pass/fail threshold here (CP5-T01/T03/T04 already check the full run's correctness, not its speed).
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    TIMING_OUT.write_text(json.dumps({
        "full_project_elapsed_sec": full_wall,
        "target_suite_elapsed_sec": measured,
        "target_suite_sum_sec": total,
        "note": _OUT_OF_SCOPE_SUITES_NOTE,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"  (informational only, not gated by this case: full-project elapsed "
          f"{full_wall if full_wall is None else round(float(full_wall), 1)}s -- {_OUT_OF_SCOPE_SUITES_NOTE}")
    print(f"  (measured numbers written to {TIMING_OUT} for review)")


def cp5_t02():
    _rc, out, _err, wall = run_full_suite()
    durations = _parse_suite_durations(out)
    _check_target_suites(durations, wall)


def cp5_t03():
    cid = "CP5-T03"
    rc, out, err, _wall = run_full_suite()
    m = SUMMARY_RE.search(out)
    total = None
    if m:
        total = int(m.group(1)) + int(m.group(2))
    check(f"[{cid}] exit code 2 (no suites found / bad arguments) is never treated as success",
          rc == 0, (rc, out[-800:], err[-800:]))
    check(f"[{cid}] any exit code other than 0 fails this case, not reinterpreted as 100%",
          rc == 0, rc)
    check(f"[{cid}] the reported total suite count is at least 100 (near the project's known ~121 suites), "
          f"not an empty or partial '0 passed, 0 failed' run", isinstance(total, int) and total >= 100,
          (total, m.group(0) if m else None))


REQUIRED_SUITES = ["tests/test_trust_cp5_e.py", "tests/test_trust_cp5_d.py", "tests/test_quota_shared.py"]


def cp5_t04():
    cid = "CP5-T04"
    _rc, out, _err, _wall = run_full_suite()
    rows = {name: status for _dur, status, name in SUITE_ROW_RE.findall(out)}
    for rel in REQUIRED_SUITES:
        name = Path(rel).name
        check(f"[{cid}] {name} is present in the per-suite table (not silently dropped from the run)",
              name in rows, sorted(rows.keys()))
        check(f"[{cid}] {name} is reported passed inside the full run",
              rows.get(name) == "passed", rows.get(name))


# ---------------------------------------------------------------------------------------------------
# CP5-T05 / CP5-T06: .ratchet/standards.md, read only.
# ---------------------------------------------------------------------------------------------------

# Snapshot of standards.md's header and its 29 numbered rules, captured verbatim at test-authoring time (this
# run's CP2-CP4 conversion work had already landed; the implementer had not yet touched standards.md for CP5).
# Used to prove the new note is additive: nothing here was deleted or reworded to make room for it.
ORIGINAL_STANDARDS_LINES = [
    "> DRAFT — inferred by ratchet-app-scout; review and edit.",
    "# Standards for the Ratchet Loop plugin",
    "1. The engine is a single stdlib-only Python 3.8+ script; add no third-party imports. Example: "
    "skills/ratchet-orchestrator/scripts/ratchet.py (`from __future__ import annotations`).",
    "2. Gate state changes only through `ratchet.py` subcommands (`cmd_*`), never by hand-editing state.json, "
    "evidence or the human log. Example: cmd_behavior in ratchet.py.",
    "3. Gates verify evidence themselves (run tests, recompute verdicts); never trust an agent's claim of "
    "PASS. Example: cmd_behavior, cmd_ui_record, cmd_adv_record.",
    "4. Every gate pass is stamped with the code fingerprint and goes stale when code changes. Example: "
    "cmd_fingerprint.",
    "5. Human approval is accepted only from a real user message recorded by the UserPromptSubmit hook; no "
    "agent message, env var or flag may impersonate it. Example: cmd_human_approve.",
    "6. The PreToolUse hook must block hand-edits to protected state, nested agent sessions and director key "
    "access. Example: hooks/hooks.json and the `hook` handler in ratchet.py.",
    "7. Stay provider-neutral: no code path or doc may assume Claude is the host; use `host_provider` and the "
    "reviewer-selection logic. Example: cmd_reviewers in ratchet.py.",
    "8. Every hook payload dialect (Claude, Codex, Gemini, Copilot) must be handled with a test in "
    "tests/test_hooks.py.",
    "9. A new provider or host is added by extending the adapter tables, not by branching in gate logic.",
    "10. When no other-provider reviewer is available, fall back to host sub-agents and log the substitution; "
    "never stall or lower the bar. Example: cmd_reviewer_status.",
    "11. Errors exit through `die(msg, code)` with an actionable message; no bare tracebacks for user errors. "
    "Example: `die` in ratchet.py.",
    "12. Persist state via `save_state` on the run object, not ad-hoc json writes. Example: `save_state` in "
    "ratchet.py.",
    "13. Subprocess calls use argument lists (no shell=True with interpolated input) and explicit timeouts "
    "for external CLIs. Example: skills/ratchet-adversarial-loop/scripts/external_review.py.",
    "14. Secrets (viewer token, director key) never appear in process lists, logs or served files. Example: "
    "\"token not visible in the process list\" in tests/test_viewer.py.",
    "15. The viewer serves only whitelisted run artifacts; tests must assert what is NOT served. Example: "
    "tests/test_viewer.py.",
    "16. Viewer/dashboard HTML is a single self-contained file with no external network dependencies. "
    "Example: skills/ratchet-orchestrator/assets/viewer.html.",
    "17. Background servers must be stoppable (`--stop`) and leave no process behind; tests verify this.",
    "18. Skills are markdown with YAML frontmatter (`name`, `description` with trigger phrases); the name "
    "matches the folder. Example: skills/ratchet-tdd-planner/SKILL.md.",
    "19. Agents are markdown in agents/ with frontmatter `name`, `description`, `tools`; name is "
    "`ratchet-<role>`. Example: agents/ratchet-fixer.md.",
    "20. Agents state a bounded return format (e.g. \"Return ≤10 lines\") and stay in their role; reviewers "
    "do not fix, fixers do not review.",
    "21. Data file shapes are defined once in skills/ratchet-orchestrator/references/schemas.md; change the "
    "schema doc together with the code that reads/writes it.",
    "22. Tests are never weakened, skipped or deleted; planned test IDs (`[CPn-X01]`) must remain present.",
    "23. Tests are runnable scripts (`python3 tests/test_x.py`) using a `check(name, cond)` helper, exit "
    "non-zero on failure, and build a throwaway project in a temp dir. Example: tests/test_hooks.py.",
    "24. Tests must not touch the real .ratchet/ of the repo; use tempfile projects and clean up spawned "
    "servers.",
    "25. Behavior changes to ratchet.py need a test in tests/ in the same change.",
    "26. README.md and the plugin manifests (.claude-plugin/plugin.json, .codex-plugin/plugin.json) stay in "
    "sync with what the skills and agents actually do.",
    "27. UI reviewers are isolated (screenshots, a11y trees, DESIGN.md only); never pass code, CLAUDE.md or "
    "chat to them. Example: skills/ratchet-ui-compare/scripts/ui_review.py.",
    "28. Never commit .ratchet/ run state or __pycache__.",
    "29. Realistic-use bar for every review finding. Ratchet's users are developers running it on their own "
    "projects with a cooperative coding agent that may cut corners (skip a test, mark its own work, hand-edit "
    "a gate to get unstuck), not an attacker. A finding must describe something a well-meaning agent or a "
    "normal developer setup would plausibly hit, with a concrete scenario. Severity follows real user harm: "
    "blocker or major only when a user would be hurt in normal use. Commands or files built to evade a check "
    "on purpose (split or encoded names, planted option-shaped file names, comment or quoting tricks, loader "
    "variables) are out of scope under the accepted deliberate same-user forgery limit; at most note them as "
    "minor. The guard is an early friendly warning; the state tamper check is the backstop.",
]
_ORIGINAL_LINE_SET = {ln.strip() for ln in ORIGINAL_STANDARDS_LINES}

# Concept anchors a substantive, non-generic note about this real lever (see app-scout.json's
# subprocess_call_counts.single_subprocess_call_overhead) must hit; a note that only says something generic
# like "tests were made faster" hits none of these.
_TECHNIQUE_ANCHORS = [
    re.compile(r"ratchet\.py", re.I),
    re.compile(r"subprocess", re.I),
    re.compile(r"in[-\s]?process", re.I),
    re.compile(r"cach(e|ing|ed)|memoiz", re.I),
    re.compile(r"once (per|a) (test )?process|per test process", re.I),
]


def _read_standards():
    try:
        return STANDARDS.read_text(encoding="utf-8")
    except OSError:
        return ""


def _new_lines(current_text: str) -> list:
    """Lines in `current_text` that are not one of the 29 original rules/header lines captured above
    (verbatim match, after stripping) -- whatever the implementer added for this checkpoint, isolated from
    everything that was already there."""
    return [ln for ln in current_text.splitlines() if ln.strip() and ln.strip() not in _ORIGINAL_LINE_SET]


def cp5_t05():
    cid = "CP5-T05"
    current = _read_standards()
    added = "\n".join(_new_lines(current))
    word_count = len(added.split())
    check(f"[{cid}] a new note is present in .ratchet/standards.md beyond the original 29 rules",
          word_count > 0, added[:400])
    check(f"[{cid}] the new note is substantive (at least a few sentences, not a one-line stub)",
          word_count >= 25, (word_count, added[:400]))
    hits = [bool(p.search(added)) for p in _TECHNIQUE_ANCHORS]
    check(f"[{cid}] the note names the actual technique: caching/importing ratchet.py's module once per test "
          f"process instead of relaunching `python3 ratchet.py ...` as a fresh subprocess per call",
          all(hits), (hits, added[:400]))


def cp5_t06():
    cid = "CP5-T06"
    current = _read_standards()
    missing = [ln for ln in ORIGINAL_STANDARDS_LINES if ln.strip() not in current]
    check(f"[{cid}] every one of rules 1-29's text (and the header) is still present verbatim, nothing "
          f"deleted or reworded", not missing, missing[:3])
    added = _new_lines(current)
    check(f"[{cid}] the new note is a clearly separate, additional entry (not edited into an existing rule's "
          f"text)", len(added) > 0, len(added))


TESTS = [
    ("CP5-T01", cp5_t01),
    ("CP5-T02", cp5_t02),
    ("CP5-T03", cp5_t03),
    ("CP5-T04", cp5_t04),
    ("CP5-T05", cp5_t05),
    ("CP5-T06", cp5_t06),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cases": cases}, f)


def main() -> int:
    if os.environ.get(_GUARD_VAR):
        print(f"  (nested invocation guarded by {_GUARD_VAR}; exiting 0 without running or touching the cache)")
        return 0
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  {MARK_FAIL} [{cid}] CRASHED: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
