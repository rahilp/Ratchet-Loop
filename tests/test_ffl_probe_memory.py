#!/usr/bin/env python3
"""
CP5 tests of the fast-follow cleanups run: a chosen model's passed test request is remembered.
   python3 tests/test_ffl_probe_memory.py

When a developer (through their agent) sets agents.reviewer to `codex/gpt-5.1`, the outside reviewer launches that model
instead of the provider's own `model` (gpt-5). Before this checkpoint the automatic test request for such a provider was
sent at every round-opening `reviewers` call and its result was kept in the project only, so a working provider was
re-tested every round, in every project. These cases pin the repaired behaviour: a PASSED test request of the chosen
model is written to the shared provider-health.json under the provider's tool plus that model (`codex:gpt-5.1`), dated
when the request was sent, and is reused by every project for probe_ttl_minutes. Nothing else changes: a failure stays
a project mark (unavailable-reviewers.json, for that model only), a pass of the chosen model never marks the provider's
own model as tested (and the own model's pass never covers the chosen one), probe_ttl_minutes=0 still tests every round,
probe_providers=false still sends nothing, a pinned or id-listed reviewer is still tested on its written model, and a
damaged shared entry is just "not fresh":
  * remembered pass: reused by a second project, by the same project's next round, re-tested once older than the window,
    still trusted just inside it, always re-tested with a window of 0 (CP5-T01 to T05);
  * a failure is never recorded as a pass and never stops another project's fresh pass (T06, T07);
  * the chosen model and the provider's own model never cover each other, nor do two chosen models (T08 to T10);
  * one call tests a chosen provider once, a pinned reviewer keeps its own model, probe_providers=false sends nothing,
    a damaged entry is not fresh and never a crash, and a pass is dated when its request was sent (T11 to T15);
  * the docs say the same thing, and the README and the test inventory list this file (T16 to T19).

Design assumptions (the production wording and code layout are not pinned beyond what the cases state):
  * a "test request" is a call the stub codex CLI logs as kind `probe` (the prompt holds "connectivity check"); the
    model it was sent on is the argument after `-m` in the logged argv;
  * the shared entry for a chosen model is keyed `<tool>:<model>`, the shape provider_health.entry_key writes
    (loaded here by path, so the key and the time format are the module's own);
  * "a second round in the same project" is opened the way tests/test_fix_round.py does it: the code changes before any
    review was written, `behavior` is run again, and `reviewers CP1 --json` opens the next round;
  * T05, T06, T08, T12 and T13 pass on the old code (they guard behaviour that must not change); T01 to T04, T07, T09,
    T10, T14, T15 (and the docs cases T16 to T19) fail on it.

Everything runs in-process through the engine's own entry points (standard 30), with the helpers of
tests/test_agents_reviewers.py and tests/test_config_layers.py: throwaway git projects that share one private
RATCHET_HOME per case, stub agent CLIs on a private PATH that log every request. No network, no real agent CLI, nothing
is spent, and the machine's real provider-health.json is never touched. This file loads provider_health.py by path, so
the test inventory must list it under Kind 1.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text

import datetime as dt
import importlib.util
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402
import trust_helpers as th                              # noqa: E402
import test_config_layers as cl                         # noqa: E402  (in-process helpers: run(), project_path(), ...)
import test_agents_config as ac                         # noqa: E402  (jload())
import test_agents_reviewers as ar                      # noqa: E402  (Stubs, STUB, FAIL_BAD_STUB, unavailable_ids)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP5-[TX]\d+)\]")

SCRIPTS = ROOT / "skills" / "ratchet-orchestrator" / "scripts"
README = README_DOCS
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
ADV_SKILL = ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"
EM = chr(0x2014)
# The number of em dashes each doc holds today (CP4 commit); the new wording may not add one.
EM_NOW = {"README.md": 11, "SKILL.md": 25, "schemas.md": 59}

CODEX = {"id": "codex", "preset": "codex", "model": "gpt-5"}      # the provider's own model is gpt-5
GEMINI = {"id": "gemini", "preset": "gemini"}


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


# ------------------------------------------------------------------------------------------------ helpers

def _load_phealth():
    spec = importlib.util.spec_from_file_location("ffl_probe_memory_phealth", SCRIPTS / "provider_health.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


phealth = _load_phealth()
K51 = phealth.entry_key("codex", "gpt-5.1")     # codex:gpt-5.1
K52 = phealth.entry_key("codex", "gpt-5.2")
K5 = phealth.entry_key("codex", "gpt-5")        # the provider's own model
KBAD = phealth.entry_key("codex", "badmodel")


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def new_env():
    """A private env (own HOME and RATCHET_HOME, host Claude Code) and a stub CLI folder first on its PATH, shared by
    every project of the case so the stubs' one log counts every test request."""
    env = th.make_env("claude")
    return env, ar.Stubs(env, ("codex", "gemini"))


def bad_codex(stubs):
    """The codex stub fails any request whose argv names badmodel (the FAIL_BAD_STUB of tests/test_agents_reviewers.py)."""
    (stubs.bin / "codex").write_text(ar.FAIL_BAD_STUB.replace("@LOG@", str(stubs.log)))


def build(env, cfg, cp_extra=None):
    """A throwaway project taken to the review step, as tests/test_agents_reviewers.py approved_world() does, but with the
    caller's env (so several projects share one RATCHET_HOME and one set of stubs) and WITHOUT opening the round.
    Returns the project dir."""
    d = Path(_tmp.mkdtemp(prefix="ratchet-ffl-pm-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    cl.project_path(d).write_text(json.dumps(dict(cfg, min_review_seconds=0)))
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": th.now_dt().isoformat()}) + "\n")

    def hook(prompt):
        return cl.run(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps(
            {"cwd": str(d), "prompt": prompt, "session_id": "s-owner", "transcript_path": str(tp)}))

    hook("please build the feature")
    r = cl.run(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, cl.text(r)
    rd = d / ".ratchet/runs/f"
    (rd / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    cp.update(cp_extra or {})
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = cl.run(d, env, "plan-submit")
    assert r.returncode == 0, cl.text(r)
    cl.run(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    hook("yes, go ahead")
    r = cl.run(d, env, "approve-plan")
    assert r.returncode == 0, cl.text(r)
    th.cp3_write_tests(d)
    for a in (("tests-written", "CP1"), ("red", "CP1")):
        r = cl.run(d, env, *a)
        assert r.returncode == 0, cl.text(r)
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    for a in (("implemented", "CP1"), ("behavior", "CP1")):
        r = cl.run(d, env, *a)
        assert r.returncode == 0, cl.text(r)
    return d


def reviewers(d, env):
    """One round-opening `reviewers CP1 --json` call. Returns (result, parsed data or {})."""
    r = cl.run(d, env, "reviewers", "CP1", "--json")
    data = ac.jload(r)
    return r, (data if isinstance(data, dict) else {})


def change_code(d, env, tag):
    """The code changes before any review was written (tests/test_fix_round.py): the next `reviewers` call replaces the
    round with a new one."""
    (Path(d) / "src/f.js").write_text(f"export const f = () => 1; // {tag}\n")
    r = cl.run(d, env, "behavior", "CP1")
    assert r.returncode == 0, cl.text(r)


def provider_cfg(chosen=None, providers=None, **extra):
    cfg = {"providers": [dict(p) for p in (providers if providers is not None else [CODEX, GEMINI])]}
    if chosen:
        cfg["agents"] = {"reviewer": chosen}
    cfg.update(extra)
    return cfg


def sent(stubs, name="codex"):
    """The model each test request the stub `name` got was sent on, in order ('' when none is named)."""
    out = []
    for _prog, _kind, argv in stubs.calls(name, "probe"):
        m = re.search(r"(?:^| )-m (\S+)", argv)
        out.append(m.group(1) if m else "")
    return out


def record_path(env):
    return Path(env["RATCHET_HOME"]) / "provider-health.json"


def record(env):
    p = record_path(env)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def seed(env, key, entry):
    """Writes one entry into the shared record the way an earlier test request would have left it (the file is read-time
    input to the engine, not engine state)."""
    p = record_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = record(env)
    data[key] = entry
    p.write_text(json.dumps(data))


def iso(t):
    return t.isoformat(timespec="microseconds")


def entry(env, key):
    e = record(env).get(key)
    return e if isinstance(e, dict) else {}


def when(e):
    """The entry's `at` as an aware time, or None."""
    at = e.get("at") if isinstance(e, dict) else None
    try:
        t = dt.datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None else None


def ok_keys(env, tool="codex"):
    """Every key of the shared record for `tool` whose last test request passed."""
    return sorted(k for k, v in record(env).items()
                  if (k == tool or k.startswith(tool + ":")) and isinstance(v, dict) and v.get("ok") is True)


def no_result(env, key):
    """The record holds no test result for `key` (a start or a pick recorded for it is not a test result)."""
    e = record(env).get(key)
    return not (isinstance(e, dict) and any(k in e for k in ("ok", "at", "detail")))


def keys_of(env, tool="codex"):
    return sorted(k for k in record(env) if k == tool or k.startswith(tool + ":"))


def ids(data):
    return [x.get("id") for x in data.get("reviewers", []) if isinstance(x, dict)]


def models(data):
    return [x.get("model") for x in data.get("reviewers", []) if isinstance(x, dict)]


def clean_run(r):
    return r.returncode == 0 and "Traceback" not in cl.text(r)


def n_probes(stubs, model=None):
    got = sent(stubs)
    return len(got) if model is None else len([m for m in got if m == model])


# ------------------------------------------------------------------------------------------------ T01 to T05

def t01_a_passed_test_is_remembered_and_reused_by_the_next_round_opening_call():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1"))
    before = utcnow()
    ra, da = reviewers(a, env)
    check("[CP5-T01] setup: the first project's round opens on the chosen codex model, with one test request on gpt-5.1",
          clean_run(ra) and ids(da) == ["codex-both"] and models(da) == ["gpt-5.1"] and sent(stubs) == ["gpt-5.1"],
          (cl.text(ra)[-300:], ids(da), models(da), sent(stubs)))
    e = entry(env, K51)
    check("[CP5-T01] the shared provider-health.json now holds an entry codex:gpt-5.1 with ok true and a non-empty detail",
          e.get("ok") is True and isinstance(e.get("detail"), str) and e.get("detail") != "", record(env))
    t = when(e)
    check("[CP5-T01] its `at` is the time the request was sent (inside this call), as a time with a zone",
          t is not None and before <= t <= utcnow(), e)
    b = build(env, provider_cfg("codex/gpt-5.1"))
    rb, db = reviewers(b, env)
    check("[CP5-T01] the second project's round opens and still lists the codex reviewer on gpt-5.1",
          clean_run(rb) and ids(db) == ["codex-both"] and models(db) == ["gpt-5.1"], (cl.text(rb)[-300:], db))
    check("[CP5-T01] the stub saw no further test request: still one in total, on gpt-5.1", sent(stubs) == ["gpt-5.1"],
          sent(stubs))


def t02_the_same_projects_next_round_does_not_resend_the_test():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1"))
    r1, d1 = reviewers(a, env)
    check("[CP5-T02] setup: round 1 sent one test request on gpt-5.1", clean_run(r1) and sent(stubs) == ["gpt-5.1"],
          (cl.text(r1)[-200:], sent(stubs)))
    change_code(a, env, "reopen")
    r2, d2 = reviewers(a, env)
    check("[CP5-T02] round 2 is open and reports the chosen model",
          clean_run(r2) and ids(d2) == ["codex-both"] and models(d2) == ["gpt-5.1"], (cl.text(r2)[-300:], d2))
    check("[CP5-T02] round 2 is a new round (the first one was replaced)",
          d1.get("round") == 1 and d2.get("round") == 2, (d1.get("round"), d2.get("round")))
    check("[CP5-T02] the stub's test-request count is still 1 (the pass is still fresh)", sent(stubs) == ["gpt-5.1"],
          sent(stubs))


def t03_a_pass_older_than_the_window_is_tested_again():
    env, stubs = new_env()
    old = utcnow() - dt.timedelta(minutes=11)
    seed(env, K51, {"ok": True, "at": iso(old), "detail": "earlier pass"})
    a = build(env, provider_cfg("codex/gpt-5.1", probe_ttl_minutes=10))
    before = utcnow()
    r, d = reviewers(a, env)
    check("[CP5-T03] the round opens on the chosen codex model", clean_run(r) and ids(d) == ["codex-both"],
          (cl.text(r)[-300:], ids(d)))
    check("[CP5-T03] exactly one new test request on gpt-5.1 reached the stub", sent(stubs) == ["gpt-5.1"], sent(stubs))
    e = entry(env, K51)
    t = when(e)
    check("[CP5-T03] the entry's `at` moved forward to this call, and ok stays true",
          e.get("ok") is True and t is not None and t > old and t >= before - dt.timedelta(seconds=1), e)


def t04_the_pass_stays_trusted_just_inside_the_window():
    env, stubs = new_env()
    young = utcnow() - dt.timedelta(minutes=9)
    seed(env, K51, {"ok": True, "at": iso(young), "detail": "earlier pass"})
    a = build(env, provider_cfg("codex/gpt-5.1", probe_ttl_minutes=10))
    r, d = reviewers(a, env)
    check("[CP5-T04] the round opens on the chosen codex model", clean_run(r) and ids(d) == ["codex-both"],
          (cl.text(r)[-300:], ids(d)))
    check("[CP5-T04] no test request is sent for a pass 9 minutes old with a 10 minute window", sent(stubs) == [], sent(stubs))
    check("[CP5-T04] the entry is left as it was", entry(env, K51).get("at") == iso(young), entry(env, K51))


def t05_a_window_of_zero_still_tests_every_round():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1", probe_ttl_minutes=0))
    r1, _ = reviewers(a, env)
    first = list(sent(stubs))
    change_code(a, env, "again")
    r2, d2 = reviewers(a, env)
    second = list(sent(stubs))
    check("[CP5-T05] both round-opening calls succeed", clean_run(r1) and clean_run(r2) and ids(d2) == ["codex-both"],
          (cl.text(r1)[-200:], cl.text(r2)[-200:]))
    check("[CP5-T05] each call sends its own test request on gpt-5.1 (the count rises with every call)",
          first == ["gpt-5.1"] and second == ["gpt-5.1", "gpt-5.1"], (first, second))


# ------------------------------------------------------------------------------------------------ T06 to T10

def t06_a_failed_test_is_not_recorded_as_a_pass():
    env, stubs = new_env()
    bad_codex(stubs)
    a = build(env, provider_cfg("codex/badmodel"))
    r, d = reviewers(a, env)
    check("[CP5-T06] the round still opens, and falls back to gemini as today", clean_run(r) and ids(d) == ["gemini-both"],
          (cl.text(r)[-300:], ids(d)))
    check("[CP5-T06] the test request on badmodel was sent and failed", any("badmodel" in c[2] for c in stubs.calls("codex", "probe")),
          stubs.calls("codex"))
    check("[CP5-T06] the shared record has no ok entry for codex:badmodel", entry(env, KBAD).get("ok") is not True,
          record(env))
    check("[CP5-T06] and no test result (ok, at or detail) for codex or codex:gpt-5 (the provider's own model)",
          no_result(env, K5) and no_result(env, "codex"), record(env))
    marks = json.loads(ar.unavailable_path(a).read_text()) if ar.unavailable_path(a).exists() else {}
    check("[CP5-T06] this project's unavailable-reviewers.json marks the codex reviewers, for badmodel only",
          marks != {} and all(k.startswith("codex-") for k in marks) and all(v.get("model") == "badmodel" for v in marks.values()),
          marks)


def t07_a_failure_in_one_project_does_not_stop_anothers_fresh_pass():
    env, stubs = new_env()
    bad_codex(stubs)
    a = build(env, provider_cfg("codex/badmodel"))
    ra, da = reviewers(a, env)
    b = build(env, provider_cfg("codex/gpt-5.1"))
    rb, db = reviewers(b, env)
    check("[CP5-T07] setup: A fell back to gemini, B opened on codex gpt-5.1 after one passing test request",
          clean_run(ra) and clean_run(rb) and ids(da) == ["gemini-both"] and ids(db) == ["codex-both"]
          and models(db) == ["gpt-5.1"] and n_probes(stubs, "gpt-5.1") == 1, (ids(da), ids(db), sent(stubs)))
    ra2, da2 = reviewers(a, env)           # A is shown again
    change_code(b, env, "next")
    rb2, db2 = reviewers(b, env)           # B opens another round
    check("[CP5-T07] A shown again and B's next round both succeed, B still on codex gpt-5.1",
          clean_run(ra2) and clean_run(rb2) and ids(db2) == ["codex-both"] and models(db2) == ["gpt-5.1"],
          (cl.text(ra2)[-200:], cl.text(rb2)[-200:], ids(db2)))
    check("[CP5-T07] B sent no new request (A's failure did not stop B's fresh pass)",
          n_probes(stubs, "gpt-5.1") == 1, sent(stubs))
    check("[CP5-T07] the shared record has an ok entry only for codex:gpt-5.1", ok_keys(env) == [K51], record(env))
    marks_a = json.loads(ar.unavailable_path(a).read_text()) if ar.unavailable_path(a).exists() else {}
    check("[CP5-T07] A's mark names badmodel only and B has no mark",
          marks_a != {} and all(v.get("model") == "badmodel" for v in marks_a.values()) and ar.unavailable_ids(b) == [],
          (marks_a, ar.unavailable_ids(b)))


def t08_a_chosen_models_pass_never_marks_the_providers_own_model_as_tested():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1"))
    ra, _ = reviewers(a, env)
    check("[CP5-T08] setup: the first project passed codex/gpt-5.1 with one test request", clean_run(ra) and sent(stubs) == ["gpt-5.1"],
          (cl.text(ra)[-200:], sent(stubs)))
    check("[CP5-T08] the record after that call has no test result (ok, at or detail) for codex:gpt-5 or bare codex",
          no_result(env, K5) and no_result(env, "codex"), record(env))
    b = build(env, provider_cfg(None, providers=[CODEX]))
    rb, db = reviewers(b, env)
    check("[CP5-T08] another project with no agents setting opens a round on codex's own model",
          clean_run(rb) and ids(db) == ["codex-both"] and models(db) == ["gpt-5"], (cl.text(rb)[-300:], db))
    check("[CP5-T08] that project sends its own test request on -m gpt-5 (the gpt-5.1 pass does not cover it)",
          sent(stubs) == ["gpt-5.1", "gpt-5"], sent(stubs))


def t09_a_fresh_pass_of_the_own_model_does_not_cover_the_chosen_model():
    env, stubs = new_env()
    own_at = iso(utcnow() - dt.timedelta(minutes=1))
    seed(env, K5, {"ok": True, "at": own_at, "detail": "written by a project with no setting"})
    a = build(env, provider_cfg("codex/gpt-5.1"))
    r, d = reviewers(a, env)
    check("[CP5-T09] the round opens on the chosen model", clean_run(r) and ids(d) == ["codex-both"] and models(d) == ["gpt-5.1"],
          (cl.text(r)[-300:], d))
    check("[CP5-T09] one test request on gpt-5.1 is sent", sent(stubs) == ["gpt-5.1"], sent(stubs))
    e = entry(env, K51)
    check("[CP5-T09] it is recorded under codex:gpt-5.1 (ok true, with a time and a detail)",
          e.get("ok") is True and when(e) is not None and bool(e.get("detail")), record(env))
    own = entry(env, K5)
    check("[CP5-T09] the codex:gpt-5 entry's ok and at are unchanged", own.get("ok") is True and own.get("at") == own_at, own)


def t10_a_pass_for_one_chosen_model_does_not_cover_another_on_the_same_provider():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1"))
    ra, _ = reviewers(a, env)
    b = build(env, provider_cfg("codex/gpt-5.2"))
    rb, db = reviewers(b, env)
    check("[CP5-T10] B opens on codex gpt-5.2 and sends one test request on gpt-5.2 (A's pass does not cover it)",
          clean_run(ra) and clean_run(rb) and ids(db) == ["codex-both"] and models(db) == ["gpt-5.2"]
          and sent(stubs) == ["gpt-5.1", "gpt-5.2"], (ids(db), models(db), sent(stubs)))
    check("[CP5-T10] the record has both codex:gpt-5.1 and codex:gpt-5.2 with ok true", ok_keys(env) == sorted([K51, K52]),
          record(env))
    change_code(a, env, "second round")
    ra2, da2 = reviewers(a, env)
    check("[CP5-T10] A's second round opens and sends nothing more",
          clean_run(ra2) and ids(da2) == ["codex-both"] and sent(stubs) == ["gpt-5.1", "gpt-5.2"], (cl.text(ra2)[-200:], sent(stubs)))


# ------------------------------------------------------------------------------------------------ T11 to T15

def t11_one_call_still_tests_a_chosen_provider_only_once():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1", providers=[CODEX]), cp_extra={"risk": "normal"})
    r, d = reviewers(a, env)
    codex_slots = [i for i in ids(d) if i.startswith("codex-")]
    check("[CP5-T11] a normal-risk step opens with two slots and the chosen codex leads both",
          clean_run(r) and len(ids(d)) == 2 and len(codex_slots) == 2, (cl.text(r)[-300:], ids(d)))
    check("[CP5-T11] exactly one test request on the chosen model, not one per slot", sent(stubs) == ["gpt-5.1"], sent(stubs))


def t12_a_pinned_reviewer_keeps_its_own_model_and_the_existing_behaviour():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1"), cp_extra={"reviewers": ["codex-both"]})
    r1, d1 = reviewers(a, env)
    change_code(a, env, "pinned again")
    r2, d2 = reviewers(a, env)
    check("[CP5-T12] both calls succeed with the pinned codex-both on its written model gpt-5",
          clean_run(r1) and clean_run(r2) and ids(d1) == ["codex-both"] and ids(d2) == ["codex-both"]
          and models(d1) == ["gpt-5"] and models(d2) == ["gpt-5"], (ids(d1), models(d1), ids(d2), models(d2)))
    check("[CP5-T12] the test requests name gpt-5 (never gpt-5.1) and were sent once while fresh, as today",
          sent(stubs) == ["gpt-5"], sent(stubs))
    check("[CP5-T12] the record holds codex:gpt-5 and no codex:gpt-5.1 entry",
          entry(env, K5).get("ok") is True and no_result(env, K51), keys_of(env))


def t13_probe_providers_false_sends_and_records_nothing():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1", probe_providers=False))
    r, d = reviewers(a, env)
    check("[CP5-T13] the round still opens on the chosen codex model", clean_run(r) and ids(d) == ["codex-both"] and models(d) == ["gpt-5.1"],
          (cl.text(r)[-300:], d))
    check("[CP5-T13] the stub saw no test request", sent(stubs) == [], sent(stubs))
    check("[CP5-T13] provider-health.json has no codex:gpt-5.1 ok entry", entry(env, K51).get("ok") is not True, record(env))


def t14_a_damaged_or_undated_shared_entry_is_not_fresh_never_a_crash():
    for label, bad in (("an entry with an unreadable time", {"ok": True, "at": "garbage"}),
                       ("an entry that is a plain string", "this is not an entry"),
                       ("an entry with ok true and no time at all", {"ok": True})):
        env, stubs = new_env()
        seed(env, K51, bad)
        a = build(env, provider_cfg("codex/gpt-5.1"))
        before = utcnow()
        r, d = reviewers(a, env)
        check(f"[CP5-T14] {label}: the command exits 0 with no traceback and the round opens",
              clean_run(r) and ids(d) == ["codex-both"], (r.returncode, cl.text(r)[-300:]))
        check(f"[CP5-T14] {label}: it sent one test request on gpt-5.1", sent(stubs) == ["gpt-5.1"], sent(stubs))
        e = entry(env, K51)
        t = when(e)
        check(f"[CP5-T14] {label}: the entry now holds a real ok true and a time from this call",
              e.get("ok") is True and t is not None and t >= before - dt.timedelta(seconds=1) and bool(e.get("detail")), record(env))


def t15_a_recorded_pass_is_dated_when_the_request_was_sent():
    env, stubs = new_env()
    a = build(env, provider_cfg("codex/gpt-5.1"))
    before = utcnow()
    r, d = reviewers(a, env)
    after = utcnow()
    e = entry(env, K51)
    t = when(e)
    check("[CP5-T15] the call passed and the shared record holds the codex:gpt-5.1 entry",
          clean_run(r) and ids(d) == ["codex-both"] and e.get("ok") is True, (cl.text(r)[-200:], record(env)))
    check("[CP5-T15] its `at` parses as a time with a zone", t is not None and t.utcoffset() is not None, e)
    check("[CP5-T15] its `at` lies between the clock read before the call and the one after it",
          t is not None and before <= t <= after, (iso(before), e.get("at"), iso(after)))
    check("[CP5-T15] it has the shape provider_health.record_probe writes (microsecond ISO time, ok, detail)",
          t is not None and e.get("at") == iso(t) and set(e) >= {"ok", "at", "detail"}, e)


# ------------------------------------------------------------------------------------------------ T16 to T19

def norm(path):
    return re.sub(r"\s+", " ", (path if isinstance(path, Path) else Path(path)).read_text())


STALE = [
    r"stays in its project,? (?:is )?(?:not shared|never written)",
    r"(?:is )?never written to the shared record",
    r"test of (?:a|the) model (?:that )?`?agents\.reviewer`? chose is the exception",
    r"chosen model[^.]{0,80}(?:kept|stays?) in (?:its|the) project (?:only|alone)",
    r"is sent again (?:each|every) (?:round|time a round opens)",
    r"(?:sent|tested) again (?:at )?(?:each|every) round",
]
START = "A passed test request of the model `agents.reviewer` chose"
# Each needle must appear in the 900 characters that start at START, in a paragraph that also names provider-health.json.
NEEDLES = {
    "the provider's tool plus that model": "kept in the shared record under the provider's tool plus that model",
    "every project / probe_ttl_minutes": None,
    "never marks the provider's own model": "never marks the provider's own model as tested",
    "a failure stays a project mark": "a failure stays a project mark",
}


def block_of(path):
    f = norm(path)
    i = f.find(START)
    return f[i:i + 900] if i >= 0 else None


def block_ok(path):
    """The chosen-model block states the new rule. Returns the list of missing pieces ([] when complete)."""
    b = block_of(path)
    if b is None:
        return [f"no sentence starting `{START}`"]
    miss = []
    for label, needle in NEEDLES.items():
        if needle is not None and needle not in b:
            miss.append(needle)
    if "every project" not in b or "probe_ttl_minutes" not in b:
        miss.append("is reused by every project for `probe_ttl_minutes`")
    return miss


def stale_in(path):
    f = norm(path)
    return [m.group(0)[:120] for pat in STALE for m in [re.search(pat, f, re.I)] if m]


def t16_schemas_documents_the_new_shared_record_entry():
    miss = block_ok(SCHEMAS)
    check("[CP5-T16] schemas.md: one sentence starting `" + START + "` says the pass is kept in the shared record under the "
          "provider's tool plus that model, is reused by every project for `probe_ttl_minutes`, never marks the provider's own "
          "model as tested, and that a failure stays a project mark", miss == [], miss)
    f = norm(SCHEMAS)
    i = f.find(START)
    para = [p for p in re.split(r"\n\s*\n", SCHEMAS.read_text()) if START in re.sub(r"\s+", " ", p)]
    check("[CP5-T16] schemas.md: that sentence is in the paragraph that describes provider-health.json",
          bool(para) and "provider-health.json" in para[0] and i >= 0, None)
    old = stale_in(SCHEMAS)
    check("[CP5-T16] schemas.md: the old wording (a chosen model's test is kept in its project only / never in the shared "
          "record / sent again each round) is gone", old == [], old)
    check("[CP5-T16] schemas.md: no em dash was added", SCHEMAS.read_text().count(EM) <= EM_NOW["schemas.md"],
          SCHEMAS.read_text().count(EM))


def t17_readme_and_both_skills_say_the_same_about_chosen_model_tests():
    for label, path, em_key, positive in (("README.md", README, "README.md", True),
                                          ("ratchet-adversarial-loop/SKILL.md", ADV_SKILL, None, True),
                                          ("ratchet-orchestrator/SKILL.md", SKILL, "SKILL.md", False)):
        old = stale_in(path)
        check(f"[CP5-T17] {label}: nothing still says the test of a chosen model is sent at every round or kept only in the project",
              old == [], old)
        if positive:
            miss = block_ok(path)
            check(f"[CP5-T17] {label}: a sentence starting `{START}` says what schemas.md says (kept in the shared record "
                  "under the provider's tool plus that model, reused by every project for `probe_ttl_minutes`, never marks "
                  "the provider's own model as tested, a failure stays a project mark)", miss == [], miss)
        if em_key:
            check(f"[CP5-T17] {label}: no em dash was added", path.read_text().count(EM) <= EM_NOW[em_key],
                  path.read_text().count(EM))


def t18_readme_running_the_tests_lists_the_new_file_exactly_once():
    text = README.read_text()
    head, _, section = text.partition("### Running the tests")
    line = "python3 tests/test_ffl_probe_memory.py"
    n = [ln.strip() for ln in section.splitlines()].count(line)
    check("[CP5-T18] the block after '### Running the tests' holds the line `" + line + "` exactly once", n == 1, n)
    check("[CP5-T18] the README names the file nowhere before that block",
          "test_ffl_probe_memory.py" not in head, None)


def t19_the_inventory_registers_this_file_because_it_loads_a_module_by_path():
    me_src = Path(__file__).read_text()
    # The live patterns of tests/test_modularize_inventory.py, assembled from pieces so this check never matches itself.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    loads, reads = bool(load_pat.search(me_src)), bool(src_pat.search(me_src))
    inv = INVENTORY.read_text()
    parts = re.split(r"(?m)^## Kind (\d)", inv)
    kinds = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    one = kinds.get("1", "")
    bullets = re.findall(r"(?m)^- `([^`]+)`", one)
    stated = re.search(r"\): (\d+) files", one[:500])
    check("[CP5-T19] this file loads a module by path and reads no engine source text, so it is a Kind 1 file", loads and not reads,
          (loads, reads))
    check("[CP5-T19] test_ffl_probe_memory.py is listed under the 'Kind 1' heading of the inventory",
          "test_ffl_probe_memory.py" in bullets, bullets[-3:])
    check("[CP5-T19] the Kind 1 heading's count equals the entries listed under it",
          stated is not None and int(stated.group(1)) == len(bullets), (stated and stated.group(1), len(bullets)))
    others = {k: v for k, v in kinds.items() if k in ("2", "3", "4")}
    check("[CP5-T19] it is not also listed under another kind",
          all("test_ffl_probe_memory.py" not in re.findall(r"(?m)^- `([^`]+)`", v) for v in others.values()), None)
    check("[CP5-T19] the earlier fast-follow files' entries are still there",
          "test_ffl_wording.py" in inv and "test_ffl_provider_shape.py" in inv, None)


TESTS = [
    ("CP5-T01", t01_a_passed_test_is_remembered_and_reused_by_the_next_round_opening_call),
    ("CP5-T02", t02_the_same_projects_next_round_does_not_resend_the_test),
    ("CP5-T03", t03_a_pass_older_than_the_window_is_tested_again),
    ("CP5-T04", t04_the_pass_stays_trusted_just_inside_the_window),
    ("CP5-T05", t05_a_window_of_zero_still_tests_every_round),
    ("CP5-T06", t06_a_failed_test_is_not_recorded_as_a_pass),
    ("CP5-T07", t07_a_failure_in_one_project_does_not_stop_anothers_fresh_pass),
    ("CP5-T08", t08_a_chosen_models_pass_never_marks_the_providers_own_model_as_tested),
    ("CP5-T09", t09_a_fresh_pass_of_the_own_model_does_not_cover_the_chosen_model),
    ("CP5-T10", t10_a_pass_for_one_chosen_model_does_not_cover_another_on_the_same_provider),
    ("CP5-T11", t11_one_call_still_tests_a_chosen_provider_only_once),
    ("CP5-T12", t12_a_pinned_reviewer_keeps_its_own_model_and_the_existing_behaviour),
    ("CP5-T13", t13_probe_providers_false_sends_and_records_nothing),
    ("CP5-T14", t14_a_damaged_or_undated_shared_entry_is_not_fresh_never_a_crash),
    ("CP5-T15", t15_a_recorded_pass_is_dated_when_the_request_was_sent),
    ("CP5-T16", t16_schemas_documents_the_new_shared_record_entry),
    ("CP5-T17", t17_readme_and_both_skills_say_the_same_about_chosen_model_tests),
    ("CP5-T18", t18_readme_running_the_tests_lists_the_new_file_exactly_once),
    ("CP5-T19", t19_the_inventory_registers_this_file_because_it_loads_a_module_by_path),
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
    global FAIL
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
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
