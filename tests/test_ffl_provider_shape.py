#!/usr/bin/env python3
"""
CP3 tests of the fast-follow cleanups run: wrong provider fields are refused, never a crash.
   python3 tests/test_ffl_provider_shape.py

A developer (through their agent) keeps provider settings in two files: the project's .ratchet/config.json and a machine-wide
config.json. A wrongly shaped provider field (for_risk written as 5, prefer_for as a plain word, stdin as "yes", ...) used to be
checked only when the machine-wide file was loaded. In the project's file it was checked only on writes, so it reached the
reviewer offer and crashed a command with a traceback. These cases pin the repaired behaviour:
  * a wrong provider field in EITHER file stops a command with one plain line that names the file's full path, the field
    (providers[0].<field>) and the shape it needs, exit code non-zero, never a traceback, and the file is left as it was
    (CP3-T01 to T05, T12 for every field the engine reads, T13 for every sub-command);
  * the repair still works while the field is bad: `config --unset providers` (project) and `config --global --unset
    providers` (machine-wide) remove the list, a repair aimed at the other file does not touch it, and a repair aimed at
    another key of the same file is refused and does not fix it (CP3-T06 to T10);
  * settings that were already valid load exactly as before (CP3-T11);
  * the README lists this file, the test inventory lists it (it parses the engine's source in T12 and T13), and the three
    docs say the field check runs at load for both files and name the repair (CP3-T14 to T16).

Design assumptions (the production wording is not pinned beyond what the cases state):
  * "one plain line" is exactly one non-empty line of stdout plus stderr for every command that refuses before it prints
    anything. Plain `status` prints its summary first and loads the settings afterwards today (also for the machine-wide
    file), so for plain `status` the refusal must be the LAST line, appear once, and name the file and field;
  * the shape words are the ones check_global_config already uses for the machine-wide file (tests/test_config_layers.py
    CP1-X17): a list, a non-empty string, a string, true or false, "a list of risk names (low, normal, high)", the lens
    names, the known presets, and the entry's id; the project's text must be the machine-wide text with another path;
  * T08 (b) and T10 are open questions in the plan: both outcomes the plan allows are accepted, and any other outcome fails.

Everything runs in-process through the engine's own entry points (standard 30), with the helpers of
tests/test_config_layers.py: a throwaway git project and a private per-user Ratchet home. No network, no real agent CLI,
nothing is spent.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text

import ast
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402,F401
import trust_helpers as th                              # noqa: E402,F401
import test_config_layers as cl                         # noqa: E402  (in-process helpers: project(), run(), ...)
import test_agents_session as se                        # noqa: E402  (clean(): pin the host)
import test_models_explain as me                        # noqa: E402  (OLD_ROLES)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP3-[TX]\d+)\]")

README = README_DOCS
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"

FOR_RISK_SHAPE = "list of risk names (low, normal, high)"


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

run, text = cl.run, cl.text


def entry(**fields):
    """A provider entry: id x on the codex preset, with the given fields added or replaced."""
    e = {"id": "x", "preset": "codex"}
    e.update(fields)
    return e


def world(proj=None, glob=None, host="claude"):
    """A throwaway git project with an active run. `proj` (a dict) is merged into the project's settings file and `glob`
    written as the machine-wide file AFTER the run was started (a bad providers field is refused by `init` itself once
    the fix is in, so it must not be there while the fixture builds)."""
    d, env = cl.project({})
    se.clean(env, host)
    if proj is not None:
        p = cl.project_path(d)
        cfg = json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}
        cfg.update(proj)
        p.write_text(json.dumps(cfg))
    if glob is not None:
        cl.put_global(env, glob)
    return d, env


def put_project(d, cfg):
    cl.project_path(d).write_text(json.dumps(cfg))


def raw(*paths):
    return [Path(p).read_bytes() if Path(p).exists() else None for p in paths]


def lines_of(r):
    return [ln for ln in text(r).splitlines() if ln.strip()]


def path_forms(path):
    return {str(path), os.path.realpath(str(path))}


def problems(r, path, key, word, preamble=False):
    """What is wrong with a call that should have been refused with one plain line: a non-zero exit code, no traceback,
    exactly one line naming the file, that line holding the field and the shape word, and (unless `preamble`, for plain
    `status`) no other line of output. With `preamble` the line must be the last one."""
    out, lines = text(r), lines_of(r)
    forms = path_forms(path)
    hit = [ln for ln in lines if any(p in ln for p in forms)]
    probs = []
    if r.returncode == 0:
        probs.append("exit code 0")
    if "Traceback" in out:
        probs.append("traceback printed")
    if len(hit) != 1:
        probs.append(f"{len(hit)} lines name the file (output {out[:160]!r})")
        return probs
    line = hit[0]
    if key not in line:
        probs.append(f"line does not name {key}")
    if word not in line:
        probs.append(f"line does not say the shape ({word})")
    if preamble:
        if lines[-1] != line:
            probs.append("the refusal is not the last line")
    elif len(lines) != 1:
        probs.append(f"{len(lines)} non-empty lines instead of 1")
    return probs


def refusal_text(r, path):
    """The refusal line with the file's path hidden, so the project-file and machine-wide texts can be compared."""
    for ln in lines_of(r):
        if any(p in ln for p in path_forms(path)):
            for p in sorted(path_forms(path), key=len, reverse=True):
                ln = ln.replace(p, "<FILE>")
            return ln
    return None


def pcfg(d):
    p = cl.project_path(d)
    return json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}


def gcfg(env):
    p = cl.global_path(env)
    return json.loads(p.read_text()) if p.exists() else {}


def adv_world(risk_reason="users could lose data if this is wrong"):
    """A throwaway project with one HIGH-risk checkpoint approved into building and taken to the adversarial step (the
    mirror of tests/test_agents_reviewers.py approved_world, with no stub CLIs and without the first `reviewers` call,
    so that call is the one the case makes). No agent CLI is installed on the private PATH. Returns (dir, env)."""
    env = th.make_env("claude")
    se.clean(env, "claude")
    d = Path(_tmp.mkdtemp(prefix="ratchet-ffl-shape-ap-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    cl.project_path(d).write_text(json.dumps({"min_review_seconds": 0}))
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": th.now_dt().isoformat()}) + "\n")

    def hook(prompt):
        return run(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps(
            {"cwd": str(d), "prompt": prompt, "session_id": "s-owner", "transcript_path": str(tp)}))

    hook("please build the feature")
    r = run(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, text(r)
    rd = d / ".ratchet/runs/f"
    (rd / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "high", "risk_reason": risk_reason,
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = run(d, env, "plan-submit")
    assert r.returncode == 0, text(r)
    run(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    hook("yes, go ahead")
    r = run(d, env, "approve-plan")
    assert r.returncode == 0, text(r)
    th.cp3_write_tests(d)
    for a in (("tests-written", "CP1"), ("red", "CP1")):
        r = run(d, env, *a)
        assert r.returncode == 0, text(r)
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    for a in (("implemented", "CP1"), ("behavior", "CP1")):
        r = run(d, env, *a)
        assert r.returncode == 0, text(r)
    return d, env


# The wrong values of T03, each with the field the line must name and the shape word it must hold.
# (label, entry fields, key, word)
WRONG = [
    ("command a string", {"command": "ls"}, "providers[0].command", "list"),
    ("command empty list", {"command": []}, "providers[0].command", "list"),
    ("command empty item", {"command": [""]}, "providers[0].command", "non-empty"),
    ("model a number", {"model": 1}, "providers[0].model", "non-empty string"),
    ("model empty", {"model": ""}, "providers[0].model", "non-empty string"),
    ("company a number", {"company": 1}, "providers[0].company", "non-empty string"),
    ("cost a number", {"cost": 1}, "providers[0].cost", "non-empty string"),
    ("variant a number", {"variant": 1}, "providers[0].variant", "a string"),
    ("stdin a word", {"stdin": "yes"}, "providers[0].stdin", "true or false"),
    ("stdin a number", {"stdin": 1}, "providers[0].stdin", "true or false"),
    ("for_risk a word", {"for_risk": "high"}, "providers[0].for_risk", FOR_RISK_SHAPE),
    ("for_risk unknown risk", {"for_risk": ["urgent"]}, "providers[0].for_risk", FOR_RISK_SHAPE),
    ("for_risk a number in a list", {"for_risk": [5]}, "providers[0].for_risk", FOR_RISK_SHAPE),
    ("for_risk a list in a list", {"for_risk": [[]]}, "providers[0].for_risk", FOR_RISK_SHAPE),
    ("prefer_for a word", {"prefer_for": "both"}, "providers[0].prefer_for", "lens names"),
    ("prefer_for unknown lens", {"prefer_for": ["nope"]}, "providers[0].prefer_for", "lens names"),
    ("prefer_for a list in a list", {"prefer_for": [["a"]]}, "providers[0].prefer_for", "lens names"),
    ("preset a number", {"preset": 5}, "providers[0].preset", "known presets"),
    ("id a number", {"id": 5}, "providers[0]", "id"),
]

# Whole-entry mistakes of T04: (label, the project's whole providers value, key, word)
WHOLE = [
    ("providers a string", "x", "providers", "list"),
    ("providers an object", {"id": "x"}, "providers", "list"),
    ("providers holds a number", [5], "providers[0]", "object"),
    ("entry without an id", [{"preset": "codex"}], "providers[0]", "id"),
    ("two entries with one id", [entry(), entry()], "providers[1].id", "twice"),
    ("entry with an unknown preset", [entry(preset="nope")], "providers[0].preset", "nope"),
    ("entry with neither preset nor command", [{"id": "x"}], "providers[0]", "preset"),
]

# One wrong value per provider field the engine reads (T12). paid_ok_at is refused in the machine-wide file only.
FIELD_WRONG = {"id": 5, "preset": 5, "command": "ls", "model": 1, "company": 1, "cost": 1, "variant": 1, "stdin": "yes",
               "for_risk": "high", "prefer_for": "both"}
FIELD_KEY_WORD = {"id": ("providers[0]", "id"), "preset": ("providers[0].preset", "known presets"),
                  "command": ("providers[0].command", "list"), "model": ("providers[0].model", "non-empty string"),
                  "company": ("providers[0].company", "non-empty string"),
                  "cost": ("providers[0].cost", "non-empty string"), "variant": ("providers[0].variant", "a string"),
                  "stdin": ("providers[0].stdin", "true or false"),
                  "for_risk": ("providers[0].for_risk", FOR_RISK_SHAPE),
                  "prefer_for": ("providers[0].prefer_for", "lens names")}
FIELD_EXEMPT = {"launch_model": "refused wherever a settings file is loaded (_check_provider_keys, in the project's file "
                                "and the machine-wide one): it exists only in memory, on a provider copy made from "
                                "agents.reviewer, so a hand-written one is never read as a setting",
                "paid_ok_at": "paid consent is accepted in the project's file (a project may hold it) and refused in the "
                              "machine-wide one by its own rule, so it has no wrong shape to refuse in the project's file"}


# ------------------------------------------------------------------------------------------------ T01 to T05

def t01_a_projects_for_risk_is_refused_in_one_line():
    cmds = (("status",), ("models",), ("models", "--json"), ("providers",), ("config",), ("config", "--sources"),
            ("config", "--scope", "project"))
    for label, val in (("5", 5), ("'high'", "high")):
        d, env = world({"providers": [entry(for_risk=val)]}, glob={})
        path = cl.project_path(d)
        before = raw(path)
        for cmd in cmds:
            r = run(d, env, *cmd)
            probs = problems(r, path, "providers[0].for_risk", FOR_RISK_SHAPE, preamble=(cmd == ("status",)))
            check(f"[CP3-T01] for_risk {label}: `{' '.join(cmd)}` is refused with one line naming the file, the field "
                  "and the shape", not probs, probs)
        check(f"[CP3-T01] for_risk {label}: the project file is byte-identical afterwards", raw(path) == before)


def t02_the_reviewer_path_is_refused_the_same_way():
    d, env = adv_world()
    path = cl.project_path(d)
    put_project(d, {"min_review_seconds": 0, "providers": [entry(for_risk=5)]})
    before = raw(path)
    for cmd in (("reviewers", "CP1"), ("reviewers", "CP1", "--json"), ("brief", "CP1", "--role", "implementer")):
        r = run(d, env, *cmd)
        out = text(r)
        probs = problems(r, path, "providers[0].for_risk", FOR_RISK_SHAPE)
        check(f"[CP3-T02] `{' '.join(cmd)}` is refused with one line naming the file, the field and the shape",
              not probs, probs)
        check(f"[CP3-T02] `{' '.join(cmd)}` does not reach the reviewer offer",
              "not set up for" not in out and "TypeError" not in out and "CP1 risk=" not in out, out[:200])
    check("[CP3-T02] the project file is byte-identical afterwards", raw(path) == before)


def t03_every_provider_field_has_its_own_refusal():
    d, env = world()
    path = cl.project_path(d)
    for label, fields, key, word in WRONG:
        put_project(d, {"providers": [entry(**fields)]})
        before = raw(path)
        r = run(d, env, "status")
        probs = problems(r, path, key, word, preamble=True)
        check(f"[CP3-T03] {label}: `status` is refused with one line naming the file, {key} and the shape",
              not probs, probs)
        check(f"[CP3-T03] {label}: nothing writes to the file", raw(path) == before)


def t04_whole_entry_mistakes_are_refused_the_same_way():
    d, env = world()
    path = cl.project_path(d)
    for label, value, key, word in WHOLE:
        put_project(d, {"providers": value})
        before = raw(path)
        for cmd in (("status",), ("models",)):
            r = run(d, env, *cmd)
            probs = problems(r, path, key, word, preamble=(cmd == ("status",)))
            check(f"[CP3-T04] {label}: `{' '.join(cmd)}` is refused with one line naming the file, {key} and the shape",
                  not probs, probs)
        check(f"[CP3-T04] {label}: the file is byte-identical afterwards", raw(path) == before)


def t05_the_same_wrong_field_in_the_machine_wide_file_gives_the_same_line():
    cases = (("for_risk a number", {"for_risk": 5}, "providers[0].for_risk", FOR_RISK_SHAPE),
             ("for_risk a word", {"for_risk": "high"}, "providers[0].for_risk", FOR_RISK_SHAPE),
             ("prefer_for a word", {"prefer_for": "both"}, "providers[0].prefer_for", "lens names"),
             ("stdin a word", {"stdin": "yes"}, "providers[0].stdin", "true or false"),
             ("command a string", {"command": "ls"}, "providers[0].command", "list"),
             ("model a number", {"model": 1}, "providers[0].model", "non-empty string"),
             ("variant a number", {"variant": 1}, "providers[0].variant", "a string"))
    for label, fields, key, word in cases:
        dg, envg = world(glob={"providers": [entry(**fields)]})
        gpath = cl.global_path(envg)
        dp, envp = world({"providers": [entry(**fields)]}, glob={})
        ppath = cl.project_path(dp)
        before = raw(gpath)
        for cmd in (("status",), ("models",), ("config",)):
            rg = run(dg, envg, *cmd)
            probs = problems(rg, gpath, key, word, preamble=(cmd == ("status",)))
            check(f"[CP3-T05] {label}: `{cmd[0]}` is refused with one line naming the machine-wide file, {key} and the "
                  "shape", not probs, probs)
            rp = run(dp, envp, *cmd)
            tg, tp_ = refusal_text(rg, gpath), refusal_text(rp, ppath)
            check(f"[CP3-T05] {label}: `{cmd[0]}` gives the same text for the project file apart from the path",
                  tg is not None and tg == tp_, (tg, tp_))
        check(f"[CP3-T05] {label}: the machine-wide file is byte-identical afterwards", raw(gpath) == before)


# ------------------------------------------------------------------------------------------------ T06 to T10

def t06_unset_providers_repairs_a_wrong_project_field():
    d, env = world({"providers": [entry(for_risk=5)], "related_tests_max": 4},
                   glob={"role_models": {"ratchet-fixer": "light"}})
    path = cl.project_path(d)
    r = run(d, env, "config", "--unset", "providers")
    out = text(r)
    check("[CP3-T06] `config --unset providers` exits 0 and prints a Removed line naming the project file",
          r.returncode == 0 and "Removed" in out and str(path) in out and "Traceback" not in out, out[:300])
    cfg = pcfg(d)
    check("[CP3-T06] the file has no providers key and related_tests_max is still 4",
          "providers" not in cfg and cfg.get("related_tests_max") == 4, cfg)
    for cmd in (("status",), ("models",), ("config",)):
        r = run(d, env, *cmd)
        check(f"[CP3-T06] `{cmd[0]}` works after the repair (exit 0, no refusal)",
              r.returncode == 0 and "Traceback" not in text(r) and "providers[0]" not in text(r), text(r)[:200])


def t07_global_unset_providers_repairs_a_wrong_machine_wide_field():
    d, env = world({}, glob={"providers": [entry(for_risk=5)], "role_models": {"ratchet-fixer": "light"}})
    gpath = cl.global_path(env)
    r = run(d, env, "config", "--global", "--unset", "providers")
    out = text(r)
    check("[CP3-T07] `config --global --unset providers` exits 0 and prints a Removed line naming the machine-wide file",
          r.returncode == 0 and "Removed" in out and str(gpath) in out and "Traceback" not in out, out[:300])
    g = gcfg(env)
    check("[CP3-T07] the machine-wide file keeps role_models and has no providers key",
          "providers" not in g and g.get("role_models") == {"ratchet-fixer": "light"}, g)
    for cmd in (("status",), ("config",)):
        r = run(d, env, *cmd)
        check(f"[CP3-T07] `{cmd[0]}` works after the repair (exit 0, no refusal)",
              r.returncode == 0 and "Traceback" not in text(r) and "providers[0]" not in text(r), text(r)[:200])


def t08_a_bad_field_in_the_file_not_written_never_blocks_the_other_files_repair():
    # (a) the project's file is bad, the machine-wide one is valid: a machine-wide write goes through
    d, env = world({"providers": [entry(for_risk=5)]}, glob={"role_models": {"ratchet-fixer": "standard"}})
    ppath, gpath = cl.project_path(d), cl.global_path(env)
    pbefore = raw(ppath)
    r = run(d, env, "config", "--global", "--set", "role_models.ratchet-fixer=light")
    check("[CP3-T08] (a) `config --global --set role_models.ratchet-fixer=light` exits 0 with the project file bad",
          r.returncode == 0 and "Traceback" not in text(r), text(r)[:300])
    check("[CP3-T08] (a) the machine-wide file now holds the new value", gcfg(env).get("role_models") ==
          {"ratchet-fixer": "light"}, gcfg(env))
    check("[CP3-T08] (a) the project file is byte-identical", raw(ppath) == pbefore)
    r = run(d, env, "status")
    check("[CP3-T08] (a) a following `status` still reports the bad project field (nothing is silently fixed)",
          r.returncode != 0 and str(ppath) in text(r) and "providers[0].for_risk" in text(r), text(r)[:300])
    # (b) the machine-wide file is bad, the project's one is valid: either the project write goes through and the bad
    # file is untouched, or it is one line naming the machine-wide file with both files unchanged (the plan's open question)
    d, env = world({"related_tests_max": 4}, glob={"providers": [entry(for_risk=5)]})
    ppath, gpath = cl.project_path(d), cl.global_path(env)
    pbefore, gbefore = raw(ppath), raw(gpath)
    r = run(d, env, "config", "--set", "related_tests_max=5")
    if r.returncode == 0:
        check("[CP3-T08] (b) the project write went through and wrote only the project file",
              pcfg(d).get("related_tests_max") == 5 and raw(gpath) == gbefore and "Traceback" not in text(r),
              (pcfg(d), text(r)[:200]))
    else:
        probs = problems(r, gpath, "providers[0].for_risk", FOR_RISK_SHAPE)
        check("[CP3-T08] (b) the project write was refused with one line naming the machine-wide file and the field",
              not probs, probs)
        check("[CP3-T08] (b) a refused write left both files byte-identical",
              raw(ppath) == pbefore and raw(gpath) == gbefore)
    r = run(d, env, "status")
    check("[CP3-T08] (b) a following `status` still reports the bad machine-wide field",
          r.returncode != 0 and str(gpath) in text(r) and "providers[0].for_risk" in text(r), text(r)[:300])
    check("[CP3-T08] (b) the machine-wide file is byte-identical afterwards", raw(gpath) == gbefore)


def t09_a_repair_aimed_at_another_key_of_the_same_file_does_not_fix_it():
    d, env = world({"providers": [entry(for_risk=5)], "related_tests_max": 4})
    path = cl.project_path(d)
    before = raw(path)
    for cmd in (("config", "--unset", "related_tests_max"), ("config", "--set", "related_tests_max=9")):
        r = run(d, env, *cmd)
        probs = problems(r, path, "providers[0].for_risk", FOR_RISK_SHAPE)
        check(f"[CP3-T09] `{' '.join(cmd)}` is refused with one line naming the project file and the field",
              not probs, probs)
        check(f"[CP3-T09] `{' '.join(cmd)}` prints no Removed or Set line",
              "Removed" not in text(r) and "Set " not in text(r), text(r)[:200])
        check(f"[CP3-T09] `{' '.join(cmd)}` leaves the file byte-identical", raw(path) == before)
    r = run(d, env, "config", "--unset", "providers")
    check("[CP3-T09] `config --unset providers` afterwards still works and removes the list",
          r.returncode == 0 and "Removed" in text(r) and "providers" not in pcfg(d) and pcfg(d).get("related_tests_max") == 4,
          (text(r)[:200], pcfg(d)))


def t10_providers_remove_with_a_bad_field_fails_plainly_or_repairs():
    d, env = world({"providers": [entry(for_risk=5)]})
    path = cl.project_path(d)
    before = raw(path)
    r = run(d, env, "providers", "--remove", "x")
    out = text(r)
    check("[CP3-T10] `providers --remove x` prints no traceback", "Traceback" not in out, out[:300])
    if r.returncode == 0:
        check("[CP3-T10] it repaired: the file no longer holds the entry",
              all(p.get("id") != "x" for p in (pcfg(d).get("providers") or [])), pcfg(d))
        r2 = run(d, env, "status")
        check("[CP3-T10] it repaired: a following `status` works", r2.returncode == 0 and "Traceback" not in text(r2),
              text(r2)[:200])
    else:
        probs = problems(r, path, "providers[0].for_risk", FOR_RISK_SHAPE)
        check("[CP3-T10] it was refused with one line naming the file and the field", not probs, probs)
        check("[CP3-T10] a refusal leaves the file byte-identical", raw(path) == before)


# ------------------------------------------------------------------------------------------------ T11

def merge2(g, p):
    """The two-layer merge the engine documents: a dict value merges one level deep, anything else replaces whole."""
    out = {}
    for layer in (g, p):
        for k, v in layer.items():
            out[k] = {**out[k], **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def t11_valid_settings_load_exactly_as_before():
    proj_providers = [
        {"id": "a", "preset": "codex"},
        {"id": "b", "command": ["ls", "-l"], "stdin": True},
        {"id": "c", "preset": "gemini", "model": "gemini-x", "company": "google", "cost": "flat", "variant": "low",
         "for_risk": ["low", "high"], "prefer_for": ["correctness"], "paid_ok_at": "2026-01-01T00:00:00Z", "note": 5},
    ]
    glob_providers = [{"id": "g", "preset": "gemini", "model": "m-1", "company": "google", "cost": "flat",
                       "for_risk": ["normal"], "prefer_for": ["both"], "stdin": False, "note": 5}]
    worlds = (("project providers over a machine-wide list", {"providers": proj_providers},
               {"providers": glob_providers, "role_models": {"ratchet-fixer": "light"}}),
              ("an empty project list", {"providers": []}, {"providers": glob_providers}),
              ("machine-wide providers only", {}, {"providers": glob_providers}),
              ("project providers only", {"providers": proj_providers}, None))
    for label, proj, glob in worlds:
        d, env = world(proj, glob=glob)
        user_p = pcfg(d)
        expected = cl.old_merge(merge2(glob or {}, user_p))
        r = run(d, env, "config")
        try:
            got = json.loads(r.stdout)
        except Exception:
            got = None
        check(f"[CP3-T11] {label}: `config` exits 0 with no traceback", r.returncode == 0 and "Traceback" not in text(r),
              text(r)[:200])
        check(f"[CP3-T11] {label}: `config` equals the pre-change merge key by key",
              isinstance(got, dict) and {k for k in got} == {k for k in expected}
              and all(got[k] == expected[k] for k in expected), sorted(k for k in expected if (got or {}).get(k) != expected[k]))
        check(f"[CP3-T11] {label}: providers come back as written (extras and paid consent kept)",
              isinstance(got, dict) and got.get("providers") == expected["providers"], (got or {}).get("providers"))
        r = run(d, env, "config", "--scope", "project")
        check(f"[CP3-T11] {label}: `config --scope project` shows the project file as written",
              r.returncode == 0 and json.loads(r.stdout or "null") == user_p, text(r)[:200])
        r = run(d, env, "models", "--json")
        try:
            data = json.loads(r.stdout)
        except Exception:
            data = None
        check(f"[CP3-T11] {label}: `models --json` exits 0 and still lists the 13 roles",
              r.returncode == 0 and isinstance(data, dict) and isinstance(data.get("roles"), dict)
              and set(data["roles"]) == set(me.OLD_ROLES), text(r)[:200])


# ------------------------------------------------------------------------------------------------ T12

def provider_fields_the_engine_reads():
    found = set()
    for path in sorted((ROOT / "skills").glob("*/scripts/*.py")):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in ("pv", "prov") \
                    and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                found.add(n.slice.value)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("get", "pop", "setdefault") \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id in ("pv", "prov") and n.args \
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                found.add(n.args[0].value)
    return found


def t12_every_provider_field_has_a_refusal_in_both_files():
    found = provider_fields_the_engine_reads()
    check("[CP3-T12] the scan finds the provider fields the engine is known to read",
          {"id", "preset", "command", "model", "stdin", "for_risk", "prefer_for"} <= found, sorted(found))
    for field, why in FIELD_EXEMPT.items():
        check(f"[CP3-T12] {field}: the exemption carries a written reason", len(why.strip()) > 40, why)
    dp, envp = world()
    dg, envg = world()
    for field in sorted(found):
        known = field in FIELD_WRONG
        check(f"[CP3-T12] {field}: has a wrong-shaped value to test with (add it, or exempt it with a reason)",
              known or field in FIELD_EXEMPT, field)
        if not known:
            continue
        key, word = FIELD_KEY_WORD[field]
        e = entry(**{field: FIELD_WRONG[field]})
        ppath, gpath = cl.project_path(dp), cl.global_path(envg)
        put_project(dp, {"providers": [e]})
        cl.put_global(envp, {})
        r = run(dp, envp, "status")
        check(f"[CP3-T12] {field}: wrong in the project file, `status` gives one line naming the file, {key} and the shape",
              not problems(r, ppath, key, word, preamble=True), problems(r, ppath, key, word, preamble=True))
        put_project(dg, {})
        cl.put_global(envg, {"providers": [e]})
        r = run(dg, envg, "status")
        check(f"[CP3-T12] {field}: wrong in the machine-wide file, `status` gives one line naming the file, {key} and "
              "the shape", not problems(r, gpath, key, word, preamble=True), problems(r, gpath, key, word, preamble=True))
    # paid consent: a project may hold it (T11 loads it); the machine-wide file refuses it by its own rule
    cl.put_global(envg, {"providers": [entry(paid_ok_at="2026-01-01")]})
    r = run(dg, envg, "status")
    check("[CP3-T12] paid_ok_at in the machine-wide file is still refused naming the file",
          r.returncode != 0 and str(cl.global_path(envg)) in text(r) and "paid_ok_at" in text(r)
          and "Traceback" not in text(r), text(r)[:200])


# ------------------------------------------------------------------------------------------------ T13

# sub-command -> the in-process arguments of its read-only or status-like form (exit 0 on a healthy project)
RUN_CMDS = {
    "status": [("status",), ("status", "--json")],
    "next": [("next",)],
    "plan-summary": [("plan-summary",)],
    "models": [("models",), ("models", "--json")],
    "providers": [("providers",), ("providers", "--json")],
    "config": [("config",), ("config", "--sources"), ("config", "--scope", "project")],
    "brief": [("brief", "CP1", "--role", "implementer")],
    "reviewers": [("reviewers", "CP1"), ("reviewers", "CP1", "--json")],
    "changes": [("changes", "CP1")],
    "deferred": [("deferred",)],
    "repeats": [("repeats",)],
    "receipt": [("receipt",)],
    "reviewer-status": [("reviewer-status",)],
    "lanes": [("lanes",)],
    "fingerprint": [("fingerprint",)],
    "doctor": [("doctor", "--json")],
    "spec": [("spec", "detect"), ("spec", "status")],
    "shortcuts": [("shortcuts",)],
}
# sub-command -> why it is not run here (every one needs a reason)
EXEMPT_CMDS = {
    "init": "starts a new run: changes state and is not a read",
    "plan-submit": "changes state: files the plan for approval",
    "approve-plan": "changes state and needs the user's recorded approval",
    "tests-written": "changes state: records the planned tests as written",
    "red": "runs the checkpoint's test command and changes state",
    "implemented": "runs the checkpoint's test command and changes state",
    "behavior": "runs the checkpoint's test command and changes state",
    "ui-record": "changes state and needs a real reviewer's result file",
    "adv-record": "changes state and needs real reviewers' result files",
    "complete": "changes state and needs every gate passed",
    "merged": "runs git and the test commands between steps, and changes state",
    "human-approve": "changes state and needs the user's recorded approval",
    "feedback": "changes state and needs the user's recorded words",
    "plan-extend": "changes state: opens a plan extension",
    "learn": "writes to learnings.md",
    "pause": "changes state: pauses the run",
    "wait": "blocks until background reviewers finish",
    "resume": "changes state: resumes a paused run",
    "ask": "records a question and changes state",
    "accept-state": "changes state and needs the user's recorded approval",
    "accept-finding": "changes state and needs the user's recorded approval",
    "accept-line": "changes state and needs the user's recorded approval",
    "raise-limit": "changes state and needs the user's recorded approval",
    "hooks-waive": "changes state and needs the user's recorded approval",
    "cancel": "abandons the run and needs the user's confirmation",
    "set-review": "changes state and needs the user's confirmation",
    "choice": "changes state and needs the user's recorded answer",
    "ui-respec": "changes state and needs the user's recorded approval",
    "approve-changes": "changes state and needs the user's recorded approval",
    "guardrail": "writes a new plan step and needs the user's recorded choice",
    "occurrences": "exits 1 with its own message when nothing was recorded for the kind it is asked about",
    "mark-fixed": "changes state: records a deferred finding as fixed",
    "check-in": "needs a decision argument and changes state; with none it exits with a usage error",
    "triage": "changes state: records fix-or-rebut decisions",
    "director-key": "director only: creates or rotates the director key",
    "director-enroll": "director only: enrolls a session",
    "lanes-start": "director only: creates branches and worktrees",
    "integrate": "director only: merges a lane into main after running the suite",
    "director-policy": "director only: exits 1 outside the director's session",
    "director-sign": "director only: signs an approval",
    "view": "starts or opens a viewer server or a browser",
    "hook": "hook emission is lane K's code and needs a host payload on stdin",
    "install-hooks": "writes the agent CLI's own configuration files",
    "ci-export": "exits 1 with its own message when the project has nothing to run",
}


def registered_subcommands():
    """Every constant name passed to `sp.add_parser(...)` in the engine's main(), found by reading its source."""
    names = []
    for n in ast.walk(ast.parse(ENGINE.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_parser" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "sp" and n.args \
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
            names.append(n.args[0].value)
    return names


def t13_no_command_prints_a_traceback_while_the_project_field_is_wrong():
    names = registered_subcommands()
    check("[CP3-T13] the scan finds the engine's registered sub-commands (at least 50, including config and reviewers)",
          len(names) >= 50 and {"config", "reviewers", "status", "models", "providers", "brief"} <= set(names), len(names))
    uncovered = [n for n in names if n not in RUN_CMDS and n not in EXEMPT_CMDS]
    check("[CP3-T13] every registered sub-command is either run here or exempt with a reason (a new command cannot skip "
          "the check)", not uncovered, uncovered)
    check("[CP3-T13] every exemption has a written reason",
          all(len(EXEMPT_CMDS[n].strip()) > 15 for n in EXEMPT_CMDS), [n for n in EXEMPT_CMDS if len(EXEMPT_CMDS[n].strip()) <= 15])
    check("[CP3-T13] no command is both run and exempt, and every listed command is registered",
          not (set(RUN_CMDS) & set(EXEMPT_CMDS)) and set(RUN_CMDS) | set(EXEMPT_CMDS) <= set(names),
          sorted((set(RUN_CMDS) | set(EXEMPT_CMDS)) - set(names)))
    d, env = adv_world()
    path = cl.project_path(d)
    put_project(d, {"min_review_seconds": 0, "providers": [entry(for_risk=5)]})
    before = raw(path)
    for name, forms in RUN_CMDS.items():
        for cmd in forms:
            r = run(d, env, *cmd)
            out = text(r)
            if r.returncode == 0:
                check(f"[CP3-T13] `{' '.join(cmd)}` exits 0 with no traceback", "Traceback" not in out, out[:200])
            else:
                probs = problems(r, path, "providers[0].for_risk", FOR_RISK_SHAPE, preamble=(cmd == ("status",)))
                check(f"[CP3-T13] `{' '.join(cmd)}` gives the one-line refusal naming the project file and the field",
                      not probs, probs)
    check("[CP3-T13] no read-only command changed the project file", raw(path) == before)
    r = run(d, env, "config", "--unset", "providers")
    check("[CP3-T13] the repair `config --unset providers` runs last and exits 0 with no traceback",
          r.returncode == 0 and "Traceback" not in text(r) and "providers" not in pcfg(d), text(r)[:200])


# ------------------------------------------------------------------------------------------------ T14 to T16

def paragraphs(md):
    return [re.sub(r"\s+", " ", ln) for ln in md.splitlines() if ln.strip()]


def t14_the_readme_lists_the_new_test_file():
    section = README.read_text().split("### Running the tests", 1)[-1]
    lines = [ln.strip() for ln in section.splitlines()]
    check("[CP3-T14] the README 'Running the tests' list holds the line `python3 tests/test_ffl_provider_shape.py`",
          "python3 tests/test_ffl_provider_shape.py" in lines, None)


def t15_the_inventory_lists_the_new_file_because_it_reads_engine_source():
    me_src = Path(__file__).read_text()
    # The live pattern of tests/test_modularize_inventory.py, assembled from pieces so this check never matches itself.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(me_src) or src_pat.search(me_src))
    inv = INVENTORY.read_text()
    listed = "test_ffl_provider_shape.py" in inv
    check("[CP3-T15] this file reads engine source (T12 and T13 parse it), so it must be in the inventory",
          scans and listed, f"scans={scans} listed={listed}")
    m = re.search(r"- `test_ffl_provider_shape\.py`[^\n]*(?:\n(?!- `|## |\n)[^\n]*)*", inv)
    entry_text = m.group(0) if m else ""
    check("[CP3-T15] its inventory entry is longer than 80 characters and mentions ratchet.py and the source scan",
          len(entry_text) > 80 and "ratchet.py" in entry_text
          and re.search(r"ast\.parse|ast\.walk|ENGINE\.read_text", entry_text) is not None, entry_text[:200])
    check("[CP3-T15] the earlier fast-follow files' inventory entries are still there (the inventory stays consistent)",
          "test_ffl_wording.py" in inv, None)


def doc_ok(md):
    """A paragraph that names both repair commands and says a wrong providers field in either file is refused at load."""
    good = []
    for p in paragraphs(md):
        if "config --unset providers" in p and "--global --unset providers" in p \
                and re.search(r"either\b[^.]{0,40}\bfiles?\b", p) and "one line" in p and "field" in p \
                and re.search(r"\bload(?:ed|s|ing)?\b", p) and re.search(r"\bshape", p):
            good.append(p)
    return good


def t16_the_docs_state_the_check_at_load_and_the_repair():
    for label, path in (("README.md", README), ("SKILL.md", SKILL), ("schemas.md", SCHEMAS)):
        md = path.read_text()
        check(f"[CP3-T16] {label}: one paragraph says a wrong-shaped providers field in either settings file is refused "
              "at load with one line naming the file and field, and names `config --unset providers` and the --global form",
              bool(doc_ok(md)), None)
        f = re.sub(r"\s+", " ", md)
        stale = re.findall(r"only the (?:machine-wide|per-user) file[^.]{0,80}\b(?:shape|checked)|"
                           r"project(?:'s)? (?:file|settings)[^.]{0,60}\b(?:is|are) not (?:shape-)?checked", f, re.I)
        check(f"[CP3-T16] {label}: nothing still says only the machine-wide file is shape-checked at load", not stale, stale[:2])


TESTS = [
    ("CP3-T01", t01_a_projects_for_risk_is_refused_in_one_line),
    ("CP3-T02", t02_the_reviewer_path_is_refused_the_same_way),
    ("CP3-T03", t03_every_provider_field_has_its_own_refusal),
    ("CP3-T04", t04_whole_entry_mistakes_are_refused_the_same_way),
    ("CP3-T05", t05_the_same_wrong_field_in_the_machine_wide_file_gives_the_same_line),
    ("CP3-T06", t06_unset_providers_repairs_a_wrong_project_field),
    ("CP3-T07", t07_global_unset_providers_repairs_a_wrong_machine_wide_field),
    ("CP3-T08", t08_a_bad_field_in_the_file_not_written_never_blocks_the_other_files_repair),
    ("CP3-T09", t09_a_repair_aimed_at_another_key_of_the_same_file_does_not_fix_it),
    ("CP3-T10", t10_providers_remove_with_a_bad_field_fails_plainly_or_repairs),
    ("CP3-T11", t11_valid_settings_load_exactly_as_before),
    ("CP3-T12", t12_every_provider_field_has_a_refusal_in_both_files),
    ("CP3-T13", t13_no_command_prints_a_traceback_while_the_project_field_is_wrong),
    ("CP3-T14", t14_the_readme_lists_the_new_test_file),
    ("CP3-T15", t15_the_inventory_lists_the_new_file_because_it_reads_engine_source),
    ("CP3-T16", t16_the_docs_state_the_check_at_load_and_the_repair),
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
