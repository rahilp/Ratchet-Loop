#!/usr/bin/env python3
"""
CP3 tests: the `agents` setting, one line per role.   python3 tests/test_agents_config.py

`agents.<role> = "<model>"` or `"<provider>/<model>"` in the project's config.json, or in the machine-wide file
under the per-user Ratchet home, picks the model for that role. A value is split at its FIRST '/', so a model name
that itself holds a '/' works; a bare model uses that role's (or the host's) current provider. `models --json` shows
the result for the sub-agent roles. A hand-edited bad role, provider, model or shape stops every command that loads
settings, naming the file and key and listing the valid choices; `config --set` refuses the same things and writes
nothing. The same role in `agents` and in the advanced `role_models` key of ONE file is refused naming both; across
files the project's choice wins whichever key it used. An unknown role in `role_models` is refused too, at both
levels (a deferred CP1 finding). A choice never approves paid use and never changes who reviews a step.

Everything is driven through the engine's own CLI entry point, in-process (standard 30), each case in its own
throwaway git project with its own private per-user Ratchet home, using the helpers in tests/test_config_layers.py.
No network, no real reviewer CLI.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP3-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import hashlib
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
import test_config_layers as cl                         # noqa: E402  (its in-process helpers: project(), run(), ...)

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


# ------------------------------------------------------------------------------------------------ helpers

run, text, project, put_global = cl.run, cl.text, cl.project, cl.put_global
global_path, project_path = cl.global_path, cl.project_path

STEMS = ("checkpoint-planner", "implementer", "fixer", "adversary", "visual-reviewer", "behavior-reviewer", "app-scout",
         "test-planner", "test-writer", "design-system", "prototyper", "test-runner", "variant-writer")


def jload(r):
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def models(d, env):
    """(roles map from `models --json` or None, the call)"""
    r = run(d, env, "models", "--json")
    data = jload(r)
    return (data.get("roles") if isinstance(data, dict) else None), r


_BASE = {}


def default_roles():
    """What `models --json` prints today with no setting anywhere."""
    if "roles" not in _BASE:
        d, env = project({})
        roles, r = models(d, env)
        assert roles, text(r)
        _BASE["roles"] = roles
    return dict(_BASE["roles"])


def pbytes(d):
    p = project_path(d)
    return p.read_bytes() if p.exists() else None


def gbytes(env):
    p = global_path(env)
    return p.read_bytes() if p.exists() else None


def pjson(d):
    return json.loads(project_path(d).read_text())


def effective(d, env):
    r = run(d, env, "config")
    return jload(r), r


def run_tree_hash(d):
    """A hash of every file under the project's runs folder, to prove a refused command changed no run file."""
    h = hashlib.sha256()
    base = Path(d) / ".ratchet" / "runs"
    for p in sorted(base.rglob("*")) if base.exists() else []:
        if p.is_file():
            h.update(str(p.relative_to(base)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def others_equal(roles, want_changes):
    """roles equals the built-in default except for the roles in want_changes."""
    want = default_roles()
    want.update(want_changes)
    return roles == want


def approved(glob_before=None, providers=None):
    """A throwaway project with one checkpoint approved into building (mirrors cl.approved_project, but the
    project's providers list is chosen by the caller). Returns (dir, env)."""
    env = th.make_env()
    if glob_before is not None:
        put_global(env, glob_before)
    d = Path(_tmp.mkdtemp(prefix="ratchet-agents-ap-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "please build the feature"},
                              "timestamp": th.now_dt().isoformat()}) + "\n")
    (d / ".ratchet").mkdir(exist_ok=True)

    def hook(prompt):
        return run(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps(
            {"cwd": str(d), "prompt": prompt, "session_id": "s-owner", "transcript_path": str(tp)}))

    hook("please build the feature")
    r = run(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, text(r)
    run(d, env, "config", "--set", "min_review_seconds=0", f"providers={json.dumps(providers or [])}")
    rd = d / ".ratchet/runs/f"
    (rd / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    assert run(d, env, "plan-submit").returncode == 0
    run(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    hook("yes, go ahead")
    r = run(d, env, "approve-plan")
    assert r.returncode == 0, text(r)
    th.cp3_write_tests(d)
    return d, env


def to_review(d, env):
    """Drive the approved run to the review step and open the round; returns (reviewer ids, the round)."""
    for a in (("tests-written", "CP1"), ("red", "CP1")):
        r = run(d, env, *a)
        assert r.returncode == 0, text(r)
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    for a in (("implemented", "CP1"), ("behavior", "CP1")):
        r = run(d, env, *a)
        assert r.returncode == 0, text(r)
    r = run(d, env, "reviewers", "CP1", "--json")
    data = jload(r)
    assert r.returncode == 0 and isinstance(data, dict), text(r)
    return [x["id"] for x in data["reviewers"]], data


# ------------------------------------------------------------------------------------------------ cases

def t01_project_names_implementer():
    d, env = project({"agents": {"implementer": "opus"}})
    roles, r = models(d, env)
    check("[CP3-T01] ratchet-implementer is opus and every other role is the built-in default",
          roles is not None and roles.get("ratchet-implementer") == "opus" and others_equal(roles, {"ratchet-implementer": "opus"}),
          (roles, text(r)[:200]))
    check("[CP3-T01] the command succeeded", r.returncode == 0, text(r)[:200])


def t02_global_file_works():
    d, env = project({}, glob={"agents": {"test-writer": "haiku"}})
    roles, r = models(d, env)
    check("[CP3-T02] ratchet-test-writer is haiku from the global file, other roles unchanged",
          roles is not None and others_equal(roles, {"ratchet-test-writer": "haiku"}), (roles, text(r)[:200]))


def t03_project_beats_global():
    d, env = project({"agents": {"implementer": "opus"}}, glob={"agents": {"implementer": "sonnet"}})
    roles, r = models(d, env)
    check("[CP3-T03] with both levels set the project's opus wins",
          roles is not None and roles.get("ratchet-implementer") == "opus", (roles, text(r)[:200]))
    r2 = run(d, env, "config", "--unset", "agents.implementer")
    roles2, r3 = models(d, env)
    check("[CP3-T03] the removal worked", r2.returncode == 0, text(r2)[:200])
    check("[CP3-T03] after the project value is removed the global sonnet shows",
          roles2 is not None and roles2.get("ratchet-implementer") == "sonnet", (roles2, text(r3)[:150]))
    check("[CP3-T03] the global file is untouched by the removal",
          json.loads(global_path(env).read_text()) == {"agents": {"implementer": "sonnet"}})


def t04_every_role_name_maps():
    # a model different from today's default for each role, alternating so a swapped or dropped line shows
    pick = {}
    for i, s in enumerate(STEMS):
        dflt = default_roles()["ratchet-" + s]
        pick[s] = [m for m in ("opus", "haiku", "sonnet") if m != dflt][i % 2]
    d, env = project({"agents": dict(pick)})
    roles, r = models(d, env)
    want = {"ratchet-" + s: m for s, m in pick.items()}
    check("[CP3-T04] each ratchet-<stem> role shows exactly its own model",
          roles == want, ({k: (roles or {}).get(k) for k in want if (roles or {}).get(k) != want[k]}, text(r)[:200]))
    check("[CP3-T04] no role was added or dropped", roles is not None and set(roles) == set(default_roles()))


def t05_no_agents_nothing_changes():
    d, env = project({})
    roles, r = models(d, env)
    check("[CP3-T05] with no agents key the roles map equals the built-in tiers resolved as today",
          roles is not None and roles == default_roles() and roles["ratchet-test-runner"] == "haiku"
          and roles["ratchet-test-writer"] == "sonnet" and roles["ratchet-adversary"] == "inherit", (roles, text(r)[:200]))
    cfg, r2 = effective(d, env)
    check("[CP3-T05] the effective config has no agents key", cfg is not None and "agents" not in cfg, text(r2)[:200])
    d2, env2 = project({}, glob={"host_provider": "auto"})
    roles2, _ = models(d2, env2)
    cfg2, _ = effective(d2, env2)
    check("[CP3-T05] a global file without agents changes nothing either",
          roles2 == default_roles() and cfg2 is not None and "agents" not in cfg2)


def t06_set_through_config_command():
    d, env = project({"related_tests_max": 4})
    r1 = run(d, env, "config", "--set", "agents.fixer=opus")
    check("[CP3-T06] project --set agents.fixer=opus exits 0", r1.returncode == 0, text(r1)[:300])
    check("[CP3-T06] it wrote only the project file", project_path(d).exists()
          and pjson(d) == {"related_tests_max": 4, "agents": {"fixer": "opus"}} and not global_path(env).exists(),
          (pbytes(d), gbytes(env)))
    pb = pbytes(d)
    r2 = run(d, env, "config", "--global", "--set", "agents.app-scout=haiku")
    check("[CP3-T06] --global --set agents.app-scout=haiku exits 0", r2.returncode == 0, text(r2)[:300])
    check("[CP3-T06] it wrote only the global file", global_path(env).exists()
          and json.loads(global_path(env).read_text()) == {"agents": {"app-scout": "haiku"}} and pbytes(d) == pb,
          (gbytes(env), pbytes(d)))
    roles, r3 = models(d, env)
    check("[CP3-T06] models --json shows both models",
          roles is not None and others_equal(roles, {"ratchet-fixer": "opus", "ratchet-app-scout": "haiku"}),
          (roles, text(r3)[:200]))


def t07_provider_model_accepted():
    d, env = project({"related_tests_max": 4})
    r = run(d, env, "config", "--set", "agents.reviewer=codex/gpt-5")
    check("[CP3-T07] agents.reviewer=codex/gpt-5 exits 0", r.returncode == 0, text(r)[:300])
    check("[CP3-T07] the project file stores \"codex/gpt-5\"",
          (pjson(d).get("agents") or {}).get("reviewer") == "codex/gpt-5", pbytes(d))
    # a model name that itself holds a '/' is kept whole (split at the first '/')
    d2, env2 = project({"related_tests_max": 4})
    r2 = run(d2, env2, "config", "--set", "agents.reviewer=codex/gpt-5/mini")
    check("[CP3-T07] a value is split at the FIRST '/': codex/gpt-5/mini is accepted and stored whole",
          r2.returncode == 0 and (pjson(d2).get("agents") or {}).get("reviewer") == "codex/gpt-5/mini",
          (r2.returncode, text(r2)[:200]))


def t08_unknown_role_refused_on_set():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    r = run(d, env, "config", "--set", "agents.implmenter=opus")
    out = text(r)
    check("[CP3-T08] the typo'd role is refused (non-zero) and named", r.returncode != 0 and "implmenter" in out, out[:300])
    check("[CP3-T08] the message lists the valid roles", all(w in out for w in
          ("implementer", "test-writer", "reviewer", "orchestrator", "director")), out[:500])
    check("[CP3-T08] no traceback and config.json is unchanged", "Traceback" not in out and pbytes(d) == pb,
          (pbytes(d), out[:200]))
    r2 = run(d, env, "config", "--global", "--set", "agents.implmenter=opus")
    check("[CP3-T08] the same typo is refused at the global level and no global file appears",
          r2.returncode != 0 and "implmenter" in text(r2) and not global_path(env).exists(), (r2.returncode, text(r2)[:200]))


def t09_unknown_provider_refused_on_set():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    r = run(d, env, "config", "--set", "agents.reviewer=nosuchco/x1")
    out = text(r)
    check("[CP3-T09] the unknown provider is refused and named", r.returncode != 0 and "nosuchco" in out, out[:300])
    check("[CP3-T09] the message lists the known provider ids", "codex" in out and "gemini" in out, out[:500])
    check("[CP3-T09] nothing was written", pbytes(d) == pb and not global_path(env).exists(), pbytes(d))


def t10_badly_written_values():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    for label, val in (("empty", ""), ("slash-first", "/opus"), ("provider-only", "codex/"), ("number", "5")):
        r = run(d, env, "config", "--set", f"agents.implementer={val}")
        out = text(r)
        check(f"[CP3-T10] {label} value {val!r} is refused", r.returncode != 0 and "Traceback" not in out,
              (r.returncode, out[:200]))
        check(f"[CP3-T10] {label}: the message shows the allowed forms (model, provider/model)",
              "provider/model" in out, out[:300])
        check(f"[CP3-T10] {label}: nothing is written", pbytes(d) == pb, pbytes(d))
    r = run(d, env, "config", "--set", "agents.implementer=a/b/c")
    check("[CP3-T10] a/b/c is refused (its first part, provider a, is unknown) and nothing is written",
          r.returncode != 0 and pbytes(d) == pb and "Traceback" not in text(r), (r.returncode, text(r)[:200]))


def t11_hand_edited_bad_value_refused_on_load():
    bad = (("provider with no model", {"agents": {"implementer": "codex/"}}, "agents.implementer"),
           ("unknown role", {"agents": {"nobody": "opus"}}, "nobody"))
    for label, cfg, key in bad:
        d, env = project(cfg)
        before = run_tree_hash(d)
        for args in (("models", "--json"), ("status",), ("reviewers", "CP1")):
            tag = f"{label}: `{' '.join(args)}`"
            r = run(d, env, *args)
            out = text(r)
            check(f"[CP3-T11] {tag} stops and names the file", r.returncode != 0 and "config.json" in out,
                  (r.returncode, out[:250]))
            check(f"[CP3-T11] {tag} names the key {key}", key in out and "Traceback" not in out, out[:250])
            check(f"[CP3-T11] {tag} lists valid choices",
                  ("implementer" in out and "test-writer" in out) if "role" in label else ("provider/model" in out),
                  out[:300])
        check(f"[CP3-T11] {label}: no run file changed", run_tree_hash(d) == before)


def t12_global_bad_value_names_global_file():
    d, env = project({"related_tests_max": 4}, glob={"agents": {"fixer": "x/y/z"}})
    r = run(d, env, "models", "--json")
    out = text(r)
    check("[CP3-T12] the load stops", r.returncode != 0 and "Traceback" not in out, out[:300])
    check("[CP3-T12] the message names the global file path", str(global_path(env)) in out, out[:400])
    check("[CP3-T12] it does not blame the project file", str(project_path(d)) not in out, out[:400])


def t13_agents_must_be_object():
    for label, val in (("a string", "opus"), ("a list", ["opus"])):
        d, env = project({"agents": val})
        r = run(d, env, "models", "--json")
        out = text(r)
        check(f"[CP3-T13] agents as {label} is refused naming the file",
              r.returncode != 0 and "config.json" in out and "Traceback" not in out, out[:300])
        check(f"[CP3-T13] agents as {label}: says agents must map role names to values",
              "agents" in out and "role name" in out, out[:300])


def t14_same_role_in_both_keys_refused():
    both = {"agents": {"implementer": "opus"}, "role_models": {"ratchet-implementer": "light"}}
    for where in ("project", "global"):
        d, env = project(both) if where == "project" else project({}, glob=both)
        r = run(d, env, "models", "--json")
        out = text(r)
        check(f"[CP3-T14] {where} file with both keys for one role: models --json refuses",
              r.returncode != 0 and "Traceback" not in out, out[:300])
        check(f"[CP3-T14] {where}: the message names both agents.implementer and role_models.ratchet-implementer",
              "agents.implementer" in out and "role_models.ratchet-implementer" in out, out[:400])
    # building the state with --set from a file that has one of the two
    d, env = project({"agents": {"implementer": "opus"}})
    pb = pbytes(d)
    r = run(d, env, "config", "--set", "role_models.ratchet-implementer=light")
    out = text(r)
    check("[CP3-T14] --set of the advanced key when the simple one is there is refused naming both",
          r.returncode != 0 and "agents.implementer" in out and "role_models.ratchet-implementer" in out, out[:400])
    check("[CP3-T14] and leaves the file unchanged", pbytes(d) == pb)
    d2, env2 = project({"role_models": {"ratchet-implementer": "light"}})
    pb2 = pbytes(d2)
    r2 = run(d2, env2, "config", "--set", "agents.implementer=opus")
    out2 = text(r2)
    check("[CP3-T14] --set of the simple key when the advanced one is there is refused naming both",
          r2.returncode != 0 and "agents.implementer" in out2 and "role_models.ratchet-implementer" in out2, out2[:400])
    check("[CP3-T14] and leaves that file unchanged", pbytes(d2) == pb2)
    d3, env3 = project({"agents": {"implementer": "opus"}})
    r3 = run(d3, env3, "config", "--global", "--set", "role_models.ratchet-implementer=light")
    check("[CP3-T14] across files (project simple, global advanced) there is no conflict: the set works",
          r3.returncode == 0, text(r3)[:300])


def t15_different_roles_fine():
    d, env = project({"agents": {"implementer": "opus"}, "role_models": {"ratchet-fixer": "light"}})
    roles, r = models(d, env)
    check("[CP3-T15] implementer is opus and fixer is the light-tier model (haiku), nothing else moved",
          roles is not None and others_equal(roles, {"ratchet-implementer": "opus", "ratchet-fixer": "haiku"}),
          (roles, text(r)[:300]))
    check("[CP3-T15] no conflict reported", r.returncode == 0 and "conflict" not in text(r).lower(), text(r)[:200])


def t16_higher_level_wins():
    d, env = project({"agents": {"implementer": "opus"}}, glob={"role_models": {"ratchet-implementer": "light"}})
    roles, r = models(d, env)
    check("[CP3-T16] A: global role_models light, project agents opus: opus",
          roles is not None and roles.get("ratchet-implementer") == "opus" and r.returncode == 0, (roles, text(r)[:200]))
    check("[CP3-T16] A: no conflict reported", "conflict" not in text(r).lower())
    d2, env2 = project({"role_models": {"ratchet-implementer": "light"}}, glob={"agents": {"implementer": "opus"}})
    roles2, r2 = models(d2, env2)
    check("[CP3-T16] B: global agents opus, project role_models light: the light-tier model (haiku)",
          roles2 is not None and roles2.get("ratchet-implementer") == "haiku" and r2.returncode == 0,
          (roles2, text(r2)[:200]))
    check("[CP3-T16] B: no conflict reported", "conflict" not in text(r2).lower())


def t17_reviewer_orchestrator_director_change_nothing():
    cfgs = {"agents": {"reviewer": "codex/gpt-5", "orchestrator": "opus", "director": "opus"}}
    d, env = project(cfgs)
    roles, r = models(d, env)
    check("[CP3-T17] no error and the roles map equals the no-agents result",
          r.returncode == 0 and roles == default_roles(), (roles, text(r)[:300]))
    prov = [{"id": "codex", "preset": "codex"}]
    base_d, base_env = approved(providers=prov)
    base_ids, _ = to_review(base_d, base_env)
    d2, env2 = approved(glob_before=cfgs, providers=prov)
    ids, _ = to_review(d2, env2)
    check("[CP3-T17] the picked reviewers and their count equal the no-agents result",
          ids == base_ids and len(ids) >= 1, (ids, base_ids))


def t18_agents_locked_at_approval():
    d, env = approved()
    p = project_path(d)
    cfg = json.loads(p.read_text())
    cfg["agents"] = {"implementer": "opus"}
    p.write_text(json.dumps(cfg))
    r = run(d, env, "tests-written", "CP1")
    out = text(r)
    check("[CP3-T18] adding agents after approval stops the gated command", r.returncode != 0, out[:300])
    check("[CP3-T18] it says the setting changed after approval and names agents",
          cl.INTEGRITY_MARK in out and "agents" in out, out[:400])
    check("[CP3-T18] it asks for approval like any other locked-setting change", "approve-changes" in out, out[:400])
    d2, env2 = approved(glob_before={"agents": {"implementer": "opus"}})
    rules = {}
    for f in sorted((d2 / ".ratchet").rglob("*.json")):
        try:
            blob = json.loads(f.read_text())
        except Exception:
            continue
        stack = [blob]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if isinstance(x.get("rules"), dict) and "role_models" in x["rules"]:
                    rules = x["rules"]
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    check("[CP3-T18] the approval snapshot lists agents among its rules", "agents" in rules, sorted(rules))
    r2 = run(d2, env2, "tests-written", "CP1")
    check("[CP3-T18] an agents line that was there at approval does not trigger a re-approval",
          r2.returncode == 0 and cl.INTEGRITY_MARK not in text(r2), text(r2)[:300])


def t19_agents_lane_local():
    keys = list(cl.engine().LANE_LOCAL_CONFIG_KEYS)
    check("[CP3-T19] the integrate lane-local key list contains agents, like role_models",
          "agents" in keys and "role_models" in keys, keys)


def t20_choice_never_approves_paid_use():
    paid = {"id": "paidy", "preset": "codex", "cost": "per-use", "model": "gpt-5"}
    d0, env0 = approved(providers=[paid])
    base_ids, _ = to_review(d0, env0)
    d, env = approved(glob_before={"agents": {"reviewer": "paidy/gpt-5"}}, providers=[paid])
    cfg, r = effective(d, env)
    provs = (cfg or {}).get("providers") or []
    check("[CP3-T20] the effective config reads and paid_ok_at is set on no provider",
          r.returncode == 0 and cfg is not None and any(p.get("id") == "paidy" for p in provs)
          and not any(p.get("paid_ok_at") for p in provs), (r.returncode, provs))
    check("[CP3-T20] paid_ok_at is set in neither file",
          "paid_ok_at" not in project_path(d).read_text() and "paid_ok_at" not in global_path(env).read_text())
    ids, _ = to_review(d, env)
    check("[CP3-T20] the paid provider is still not used without the developer's own reply",
          not any(i.startswith("paidy") for i in ids) and ids == base_ids, (ids, base_ids))
    check("[CP3-T20] paid_ok_at is still set nowhere after the review round opened",
          "paid_ok_at" not in project_path(d).read_text() and "paid_ok_at" not in global_path(env).read_text())


def t21_earlier_steps_hold():
    d, env = project({"related_tests_max": 4}, glob={"role_models": {"ratchet-implementer": "opus"}})
    pb, gb = pbytes(d), gbytes(env)
    r = run(d, env, "config", "--set", "foo")
    out = text(r)
    check("[CP3-T21] `--set foo` (no '=') is still refused naming key=value",
          r.returncode != 0 and "key=value" in out and "Traceback" not in out, out[:300])
    check("[CP3-T21] nothing was written", pbytes(d) == pb and gbytes(env) == gb)
    roles, r2 = models(d, env)
    check("[CP3-T21] the global role_models value still shows",
          roles is not None and others_equal(roles, {"ratchet-implementer": "opus"}), (roles, text(r2)[:200]))


def t22_readme_lists_file():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("### Running the tests", 1)[-1]
    check("[CP3-T22] the README 'Running the tests' block lists python3 tests/test_agents_config.py",
          re.search(r"^python3 tests/test_agents_config\.py$", section, re.M) is not None)


def t23_inventory_lists_file():
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    check("[CP3-T23] the test inventory registers tests/test_agents_config.py", "test_agents_config.py" in inv)


# ---- extra cases: the director's rulings that the plan's cases do not pin on their own

def x01_unknown_role_models_key_refused():
    typo = {"role_models": {"ratchet-implmenter": "light"}}
    d, env = project(typo)
    before = run_tree_hash(d)
    r = run(d, env, "models", "--json")
    out = text(r)
    check("[CP3-X01] a typo'd role_models key in the project file stops the load, naming file and key",
          r.returncode != 0 and "config.json" in out and "ratchet-implmenter" in out and "Traceback" not in out, out[:300])
    check("[CP3-X01] project: the message lists the valid roles",
          "ratchet-implementer" in out and "ratchet-fixer" in out, out[:500])
    check("[CP3-X01] project: no run file changed", run_tree_hash(d) == before)
    d2, env2 = project({"related_tests_max": 4}, glob=typo)
    r2 = run(d2, env2, "models", "--json")
    out2 = text(r2)
    check("[CP3-X01] a typo'd role_models key in the global file stops the load naming the global file",
          r2.returncode != 0 and str(global_path(env2)) in out2 and "ratchet-implmenter" in out2, out2[:300])
    check("[CP3-X01] global: the message lists the valid roles and does not blame the project file",
          "ratchet-implementer" in out2 and "ratchet-fixer" in out2 and str(project_path(d2)) not in out2, out2[:500])
    d3, env3 = project({"related_tests_max": 4})
    pb = pbytes(d3)
    r3 = run(d3, env3, "config", "--set", "role_models.ratchet-implmenter=light")
    out3 = text(r3)
    check("[CP3-X01] --set of a typo'd role_models key is refused listing the valid roles, nothing written",
          r3.returncode != 0 and "ratchet-implmenter" in out3 and "ratchet-implementer" in out3 and pbytes(d3) == pb,
          out3[:400])
    r4 = run(d3, env3, "config", "--global", "--set", "role_models.ratchet-implmenter=light")
    check("[CP3-X01] --global --set of a typo'd role_models key is refused, no global file appears",
          r4.returncode != 0 and "ratchet-implmenter" in text(r4) and not global_path(env3).exists(), text(r4)[:300])
    d5, env5 = project({"related_tests_max": 4})
    r5 = run(d5, env5, "config", "--set", "role_models.ratchet-fixer=light")
    check("[CP3-X01] a real role in role_models is still accepted", r5.returncode == 0, text(r5)[:300])


def x02_bare_model_and_unknown_model():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    r = run(d, env, "config", "--set", "agents.implementer=nosuchmodel")
    out = text(r)
    check("[CP3-X02] an unknown bare model is refused, named, with the valid choices listed",
          r.returncode != 0 and "nosuchmodel" in out and "opus" in out and "sonnet" in out and "haiku" in out, out[:400])
    check("[CP3-X02] nothing was written", pbytes(d) == pb)
    d3, env3 = project({"related_tests_max": 4})
    r3 = run(d3, env3, "config", "--set", "agents.implementer=opencode-go/glm-5.3")
    check("[CP3-X02] a value with an unknown first part is refused on the provider (split at the first '/')",
          r3.returncode != 0 and "opencode-go" in text(r3), text(r3)[:300])


def x03_sub_agent_choices_keep_reviewers():
    prov = [{"id": "codex", "preset": "codex"}]
    d0, env0 = approved(providers=prov)
    base, _ = to_review(d0, env0)
    d, env = approved(glob_before={"agents": {"implementer": "opus", "fixer": "haiku", "adversary": "haiku"}},
                      providers=prov)
    ids, _ = to_review(d, env)
    check("[CP3-X03] sub-agent role choices do not change who reviews or how many",
          ids == base and len(ids) >= 1, (ids, base))


BAD_AGENT_VALUES = (("5", 5), ("null", None), ("true", True), ("[]", []), ("{}", {}), ("empty", ""),
                    ("codex/", "codex/"), ("/opus", "/opus"))


def x04_bad_value_refused_on_load_both_levels():
    for label, val in BAD_AGENT_VALUES:
        for level in ("project", "global"):
            if level == "project":
                d, env = project({"agents": {"implementer": val}})
            else:
                d, env = project({"related_tests_max": 4}, glob={"agents": {"implementer": val}})
            where = str(project_path(d) if level == "project" else global_path(env))
            for args in (("status",), ("models", "--json")):
                r = run(d, env, *args)
                out = text(r)
                check(f"[CP3-X04] {level} value {label}: `{' '.join(args)}` stops, names the file and key, no traceback",
                      r.returncode != 0 and where in out and "agents.implementer" in out and "Traceback" not in out
                      and "provider/model" in out, (r.returncode, out[:250]))


def x05_bad_value_refused_on_set():
    for label, val in (("empty", ""), ("codex/", "codex/"), ("/opus", "/opus")):
        for flags in ((), ("--global",)):
            d, env = project({"related_tests_max": 4})
            pb = pbytes(d)
            r = run(d, env, "config", *flags, "--set", f"agents.implementer={val}")
            out = text(r)
            check(f"[CP3-X05] --set {label!r} {' '.join(flags) or 'project'} is refused, no traceback, nothing written",
                  r.returncode != 0 and "Traceback" not in out and "agents.implementer" in out
                  and pbytes(d) == pb and not global_path(env).exists(), (r.returncode, out[:250]))


TESTS = [
    ("CP3-T01", t01_project_names_implementer), ("CP3-T02", t02_global_file_works),
    ("CP3-T03", t03_project_beats_global), ("CP3-T04", t04_every_role_name_maps),
    ("CP3-T05", t05_no_agents_nothing_changes), ("CP3-T06", t06_set_through_config_command),
    ("CP3-T07", t07_provider_model_accepted), ("CP3-T08", t08_unknown_role_refused_on_set),
    ("CP3-T09", t09_unknown_provider_refused_on_set), ("CP3-T10", t10_badly_written_values),
    ("CP3-T11", t11_hand_edited_bad_value_refused_on_load), ("CP3-T12", t12_global_bad_value_names_global_file),
    ("CP3-T13", t13_agents_must_be_object), ("CP3-T14", t14_same_role_in_both_keys_refused),
    ("CP3-T15", t15_different_roles_fine), ("CP3-T16", t16_higher_level_wins),
    ("CP3-T17", t17_reviewer_orchestrator_director_change_nothing), ("CP3-T18", t18_agents_locked_at_approval),
    ("CP3-T19", t19_agents_lane_local), ("CP3-T20", t20_choice_never_approves_paid_use),
    ("CP3-T21", t21_earlier_steps_hold), ("CP3-T22", t22_readme_lists_file), ("CP3-T23", t23_inventory_lists_file),
    ("CP3-X01", x01_unknown_role_models_key_refused), ("CP3-X02", x02_bare_model_and_unknown_model),
    ("CP3-X03", x03_sub_agent_choices_keep_reviewers),
    ("CP3-X04", x04_bad_value_refused_on_load_both_levels), ("CP3-X05", x05_bad_value_refused_on_set),
]


def x06_host_bare_models():
    d, env = project({"related_tests_max": 4})
    env["RATCHET_HOST_PROVIDER"] = "openai"
    r = run(d, env, "config", "--set", "agents.implementer=gpt-5.5")
    roles, _ = models(d, env)
    check("[CP3-X06] a Codex host accepts its own bare model and applies it",
          r.returncode == 0 and roles and roles.get("ratchet-implementer") == "gpt-5.5", (text(r)[:200], roles))
    d2, env2 = project({"related_tests_max": 4})
    env2["RATCHET_HOST_PROVIDER"] = "anthropic"
    pb = pbytes(d2)
    r2 = run(d2, env2, "config", "--set", "agents.implementer=gpt-5.5")
    out = text(r2)
    check("[CP3-X06] a Claude host refuses another host's bare model, naming the host and the usable models",
          r2.returncode != 0 and "anthropic" in out and "opus" in out and "provider/model" in out and pbytes(d2) == pb,
          out[:300])
    d3, env3 = project({"agents": {"implementer": "opus"}})
    env3["RATCHET_HOST_PROVIDER"] = "anthropic"
    r3 = run(d3, env3, "models", "--json")
    check("[CP3-X06] a Claude host still applies a Claude model", (jload(r3) or {}).get("roles", {}).get(
        "ratchet-implementer") == "opus", text(r3)[:200])


def x07_inapplicable_agents_keeps_lower_role_models():
    d, env = project({"agents": {"implementer": "codex/gpt-5"}}, glob={"role_models": {"ratchet-implementer": "light"}})
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    roles, r = models(d, env)
    check("[CP3-X07] an agents entry that cannot apply leaves the global role_models value in effect",
          roles and roles.get("ratchet-implementer") == "haiku", (roles or {}).get("ratchet-implementer"))
    check("[CP3-X07] models prints a one-line note naming the role",
          "agents.implementer" in text(r) and "ratchet-implementer" in text(r), text(r)[:300])


def x08_set_prints_note_and_readme_sentence():
    d, env = project({"related_tests_max": 4})
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    r = run(d, env, "config", "--set", "agents.fixer=codex/gpt-5")
    out = text(r)
    check("[CP3-X08] config --set succeeds and prints a note that the choice cannot apply",
          r.returncode == 0 and "agents.fixer" in out and "ratchet-fixer" in out and "can't apply" in out, out[:300])
    readme = (ROOT / "README.md").read_text()
    check("[CP3-X08] the README says an unknown role in role_models stops the command",
          "unknown role in `role_models`" in readme and "stops the command" in readme)


TESTS += [("CP3-X06", x06_host_bare_models), ("CP3-X07", x07_inapplicable_agents_keeps_lower_role_models),
          ("CP3-X08", x08_set_prints_note_and_readme_sentence)]


def _load_engine():
    sys.path.insert(0, str(ROOT / "skills" / "ratchet-orchestrator" / "scripts"))
    import ratchet as eng
    return eng


def x09_resolve_role_layers():
    eng = _load_engine()
    os.environ["RATCHET_HOST_PROVIDER"] = "anthropic"
    try:
        g, p = ("global", {"agents": {"implementer": "sonnet"}}), ("project", {"agents": {"implementer": "opus"}})
        r = eng.resolve_role([g, p], "ratchet-implementer")
        check("[CP3-X09] project agents beats global agents", r and r["used"] == "opus" and r["source"] == "project"
              and r["asked"] == "opus" and r["reason"] == "", r)
        g2 = ("global", {"agents": {"implementer": "opus"}})
        p2 = ("project", {"role_models": {"ratchet-implementer": "light"}})
        r = eng.resolve_role([g2, p2], "ratchet-implementer")
        check("[CP3-X09] project role_models beats global agents", r and r["used"] == "haiku"
              and r["source"] == "project", r)
        r = eng.resolve_role([("global", {"role_models": {"ratchet-implementer": "light"}}),
                              ("project", {"agents": {"implementer": "opus"}})], "ratchet-implementer")
        check("[CP3-X09] project agents beats global role_models", r and r["used"] == "opus"
              and r["source"] == "project", r)
        r = eng.resolve_role([("global", {})], "ratchet-fixer")
        check("[CP3-X09] nothing set: the default, source default", r and r["source"] == "default"
              and r["asked"] is None, r)
    finally:
        os.environ.pop("RATCHET_HOST_PROVIDER", None)


def x10_resolve_role_fallback_and_unknown():
    eng = _load_engine()
    os.environ["RATCHET_HOST_PROVIDER"] = "anthropic"
    try:
        low = ("global", {"role_models": {"ratchet-implementer": "light"}})
        top = ("project", {"agents": {"implementer": "codex/gpt-5"}})
        r = eng.resolve_role([low, top], "ratchet-implementer")
        check("[CP3-X10] an agents entry that cannot apply falls back to the lower role_models value",
              r and r["used"] == "haiku" and r["asked"] == "codex/gpt-5" and r["source"] == "project", r)
        check("[CP3-X10] the result gives a reason naming the host", r and "can't apply" in r["reason"]
              and "anthropic" in r["reason"], r)
        r = eng.resolve_role([top], "ratchet-implementer")
        check("[CP3-X10] with nothing below, the fallback is the shipped default", r and r["used"] == "inherit"
              and r["reason"], r)
        check("[CP3-X10] an unknown role is not resolvable", eng.resolve_role([low, top], "ratchet-nobody") is None)
    finally:
        os.environ.pop("RATCHET_HOST_PROVIDER", None)


TESTS += [("CP3-X09", x09_resolve_role_layers), ("CP3-X10", x10_resolve_role_fallback_and_unknown)]


def x11_global_file_loads_on_every_host():
    glob = {"host_models": {"openai": {"strong": "gpt-5.5", "standard": "gpt-5.5-mini"}},
            "agents": {"implementer": "gpt-5.5"}}
    d, env = project({"related_tests_max": 4}, glob=glob)
    env["RATCHET_HOST_PROVIDER"] = "openai"
    roles, r = models(d, env)
    check("[CP3-X11] the openai host applies the machine-wide bare model",
          r.returncode == 0 and roles and roles.get("ratchet-implementer") == "gpt-5.5", text(r)[:200])
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    roles, r = models(d, env)
    check("[CP3-X11] the same file loads on the anthropic host without stopping, the role keeps its default",
          r.returncode == 0 and roles and roles.get("ratchet-implementer") == "inherit", text(r)[:300])
    check("[CP3-X11] the anthropic host prints the one-line note naming role and host",
          "agents.implementer" in text(r) and "ratchet-implementer" in text(r) and "anthropic" in text(r)
          and "can't apply" in text(r), text(r)[:300])
    d2, env2 = project({"related_tests_max": 4}, glob={"agents": {"implementer": "opus"}})
    env2["RATCHET_HOST_PROVIDER"] = "openai"
    roles, r = models(d2, env2)
    check("[CP3-X11] an anthropic model in the machine-wide file is not applied on a host with no model list, with a note",
          r.returncode == 0 and roles and roles.get("ratchet-implementer") == "inherit"
          and "agents.implementer" in text(r), text(r)[:300])


def x12_set_of_host_specific_model_refused_on_wrong_host():
    d, env = project({"related_tests_max": 4})
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    for flags in ((), ("--global",)):
        pb, gb = pbytes(d), gbytes(env)
        r = run(d, env, "config", *flags, "--set", "agents.implementer=gpt-5.5")
        out = text(r)
        check(f"[CP3-X12] {' '.join(flags) or 'project'} --set of another host's model is refused naming the host",
              r.returncode != 0 and "anthropic" in out and "agents.implementer" in out and pbytes(d) == pb
              and gbytes(env) == gb, out[:300])
    d2, env2 = project({"related_tests_max": 4})
    env2["RATCHET_HOST_PROVIDER"] = "openai"
    r = run(d2, env2, "config", "--set", "agents.implementer=opus")
    check("[CP3-X12] on a host with no model list, any well-formed bare name is accepted by --set (the engine cannot list that host's models)",
          r.returncode == 0 and "agents.implementer" in text(r), text(r)[:300])


def x13_typo_bare_name_noted_at_load_refused_at_set():
    d, env = project({"related_tests_max": 4}, glob={"agents": {"implementer": "opsu"}})
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    r = run(d, env, "models", "--json")
    out = text(r)
    check("[CP3-X13] a bare name no host or provider names loads with the one-line note naming the key; the role keeps its default",
          r.returncode == 0 and "agents.implementer" in out and "opsu" in out and "can't apply" in out
          and (jload(r) or {}).get("roles", {}).get("ratchet-implementer") == "inherit" and "Traceback" not in out,
          out[:300])
    d2, env2 = project({"agents": {"fixer": "sonet"}})
    env2["RATCHET_HOST_PROVIDER"] = "anthropic"
    r2 = run(d2, env2, "models", "--json")
    check("[CP3-X13] the same in the project file", r2.returncode == 0 and "sonet" in text(r2)
          and "agents.fixer" in text(r2), text(r2)[:300])
    d3, env3 = project({"related_tests_max": 4})
    env3["RATCHET_HOST_PROVIDER"] = "anthropic"
    pb = pbytes(d3)
    r3 = run(d3, env3, "config", "--set", "agents.implementer=opsu")
    check("[CP3-X13] config --set of a name no host or provider names is refused on a host with a model list",
          r3.returncode != 0 and "opsu" in text(r3) and pbytes(d3) == pb, text(r3)[:300])


def x14_resolve_role_used_matches_models_json():
    eng = _load_engine()
    d, env = project({"related_tests_max": 4})
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    roles, r = models(d, env)
    os.environ["RATCHET_HOST_PROVIDER"] = "anthropic"
    try:
        bad = {}
        for role, want in (roles or {}).items():
            res = eng.resolve_role([("global", {}), ("project", {})], role)
            if not res or res["used"] != want:
                bad[role] = (res or {}).get("used"), want
        check("[CP3-X14] with nothing set, resolve_role's used matches models --json for every role",
              roles and not bad, bad or text(r)[:200])
        res = eng.resolve_role([("global", {}), ("project", {})], "ratchet-test-planner")
        check("[CP3-X14] test-planner reports sonnet", res and res["used"] == "sonnet", res)
        res = eng.resolve_role([("global", {}), ("project", {})], "ratchet-test-runner")
        check("[CP3-X14] test-runner reports haiku", res and res["used"] == "haiku", res)
        res = eng.resolve_role([("project", {"agents": {"test-planner": "codex/gpt-5"}})], "ratchet-test-planner")
        check("[CP3-X14] an agents choice that cannot apply falls back to the shipped model, not inherit",
              res and res["used"] == "sonnet" and res["reason"], res)
    finally:
        os.environ.pop("RATCHET_HOST_PROVIDER", None)


TESTS += [("CP3-X11", x11_global_file_loads_on_every_host),
          ("CP3-X12", x12_set_of_host_specific_model_refused_on_wrong_host),
          ("CP3-X13", x13_typo_bare_name_noted_at_load_refused_at_set), ("CP3-X14", x14_resolve_role_used_matches_models_json)]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = [{"id": cid, "status": "passed" if CASE_STATUS.get(cid, False) else "failed",
              "message": "" if CASE_STATUS.get(cid, False) else "; ".join(CASE_MESSAGES.get(cid, []))[:300]}
             for cid, _ in TESTS]
    with open(path, "w") as f:
        json.dump({"cases": cases}, f)


def x15_same_global_file_every_host():
    glob = {"agents": {"implementer": "gpt-5.5"}}
    d, env = project({"related_tests_max": 4}, glob=glob)
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    r = run(d, env, "models", "--json")
    check("[CP3-X15] the same global file loads on an anthropic host with a note, the role keeps its default",
          r.returncode == 0 and "agents.implementer" in text(r) and "can't apply" in text(r)
          and (jload(r) or {}).get("roles", {}).get("ratchet-implementer") == "inherit", text(r)[:300])
    env["RATCHET_HOST_PROVIDER"] = "openai"
    r = run(d, env, "models", "--json")
    check("[CP3-X15] the same file applies on an openai host",
          r.returncode == 0 and (jload(r) or {}).get("roles", {}).get("ratchet-implementer") == "gpt-5.5",
          text(r)[:300])


TESTS += [("CP3-X15", x15_same_global_file_every_host)]


def x16_strict_refusal_only_for_the_layer_being_written():
    glob = {"agents": {"implementer": "gpt-5.5", "fixer": "opsu"}}
    d, env = project({"related_tests_max": 4}, glob=glob)
    env["RATCHET_HOST_PROVIDER"] = "anthropic"
    r = run(d, env, "config", "--set", "agents.implementer=opus")
    check("[CP3-X16] a foreign model and a typo in the global file do not block a project --set",
          r.returncode == 0 and (pjson(d).get("agents") or {}).get("implementer") == "opus", text(r)[:300])
    r = run(d, env, "config", "--unset", "agents.implementer")
    check("[CP3-X16] the same for a project --unset",
          r.returncode == 0 and "implementer" not in (pjson(d).get("agents") or {}), text(r)[:300])
    gb = gbytes(env)
    r = run(d, env, "config", "--global", "--set", "agents.implementer=gpt-5.5")
    check("[CP3-X16] --global --set of a foreign model on an anthropic host is still refused naming the host",
          r.returncode != 0 and "anthropic" in text(r) and gbytes(env) == gb, text(r)[:300])
    d2, env2 = project({"related_tests_max": 4, "agents": {"implementer": "gpt-5.5"}})
    env2["RATCHET_HOST_PROVIDER"] = "anthropic"
    r = run(d2, env2, "config", "--global", "--set", "agents.fixer=opus")
    check("[CP3-X16] a foreign model in the project file does not block a global write",
          r.returncode == 0, text(r)[:300])


TESTS += [("CP3-X16", x16_strict_refusal_only_for_the_layer_being_written)]


def x17_old_approval_without_agents_rule():
    d, env = approved()
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("[CP3-X17] setup: the approval snapshot holds no agents rule while agents is unset (as one taken before "
          "`agents` existed)", "agents" not in st["approved"]["rules"], list(st["approved"]["rules"])[:5])
    r = run(d, env, "tests-written", "CP1")
    check("[CP3-X17] an approval taken before `agents` existed, with agents still unset, reports no agents change",
          "config rule 'agents'" not in text(r), text(r)[:300])
    run(d, env, "config", "--set", "agents.implementer=opus")
    r = run(d, env, "tests-written", "CP1")
    check("[CP3-X17] the same old approval after agents is set reports the agents rule",
          "config rule 'agents'" in text(r), text(r)[:300])


TESTS += [("CP3-X17", x17_old_approval_without_agents_rule)]


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

