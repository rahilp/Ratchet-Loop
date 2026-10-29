#!/usr/bin/env python3
"""
CP2 tests: read and write each config level.   python3 tests/test_config_scopes.py

`ratchet.py config` shows the settings at one level (`--scope global|project|effective`), says which level each
top-level key came from (`--sources`), and writes or removes a value at the project level or, with `--global`, in
the machine-wide file (`--set key=value`, `--unset key`). A bad argument, a refused value or a damaged machine-wide
file stops the command before anything is written. With no flag the command prints what it always printed.

The machine-wide file only holds role_models, host_models, providers, agents, host_provider and ui_reviewers (CP1),
so the cases that need a key set at both levels use host_provider (a scalar both levels may hold) and role_models
(a map both levels may hold); project-only keys such as related_tests_max and quota_retest_minutes stand for the
"project beats built-in" side.

Everything is driven through the engine's own CLI entry point, in-process (standard 30), each case in its own
throwaway git project with its own private RATCHET_HOME. No network, no real reviewer CLI.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

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
import test_config_layers as cl                         # noqa: E402  (its in-process helpers: project(), run(), ...)

ENGINE_PATH = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP2-[TX]\d+)\]")
_DEFAULTS = {}


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


def engine_defaults():
    """The shipped DEFAULT_CONFIG and the global allow-list, from a module loaded straight from the engine file."""
    if not _DEFAULTS:
        spec = importlib.util.spec_from_file_location("ratchet_cfgscopes_defaults", ENGINE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _DEFAULTS["cfg"] = json.loads(json.dumps(mod.DEFAULT_CONFIG))
        _DEFAULTS["global_keys"] = tuple(getattr(mod, "GLOBAL_ALLOWED_KEYS", ()))
    return _DEFAULTS


def jload(r):
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def pbytes(d):
    p = project_path(d)
    return p.read_bytes() if p.exists() else None


def gbytes(env):
    p = global_path(env)
    return p.read_bytes() if p.exists() else None


def pjson(d):
    return json.loads(project_path(d).read_text())


def sources(d, env):
    """The `--sources` output as {top-level key: set of level words found for it}. Accepts a JSON object
    (key -> level, or key -> object/list holding the level) or one line per key."""
    r = run(d, env, "config", "--sources")
    levels = ("builtin", "global", "project")
    out = {}
    data = jload(r)
    if isinstance(data, dict):
        for k, v in data.items():
            blob = json.dumps(v)
            out[k] = {w for w in levels if re.search(rf"\b{w}\b", blob)}
    else:
        for line in r.stdout.splitlines():
            for k in effective_keys():
                if re.search(rf"(?<![\w.]){re.escape(k)}(?![\w.])", line):
                    out.setdefault(k, set()).update(w for w in levels if re.search(rf"\b{w}\b", line))
    return out, r


def effective_keys():
    return list(engine_defaults()["cfg"])


def effective(d, env):
    r = run(d, env, "config", "--scope", "effective")
    return jload(r), r


# ------------------------------------------------------------------------------------------------ cases

def t01_no_flags_unchanged():
    proj = {"related_tests_max": 7}
    d, env = project(proj)
    r = run(d, env, "config")
    want = json.dumps(cl.old_merge(proj), indent=2) + "\n"
    check("[CP2-T01] `config` with no flags prints the merged settings exactly as before", r.stdout == want and r.returncode == 0,
          (r.returncode, text(r)[:200]))


def _two_levels():
    return project({"host_provider": "google", "related_tests_max": 3}, glob={"host_provider": "openai"})


def t02_scope_global():
    d, env = _two_levels()
    r = run(d, env, "config", "--scope", "global")
    check("[CP2-T02] --scope global prints exactly the global file", r.returncode == 0 and jload(r) == {"host_provider": "openai"},
          (r.returncode, text(r)[:200]))
    check("[CP2-T02] no project-only or built-in key in it", "related_tests_max" not in r.stdout and "review_depth" not in r.stdout)


def t03_scope_project():
    d, env = _two_levels()
    r = run(d, env, "config", "--scope", "project")
    check("[CP2-T03] --scope project prints exactly the project file",
          r.returncode == 0 and jload(r) == {"host_provider": "google", "related_tests_max": 3}, (r.returncode, text(r)[:200]))
    check("[CP2-T03] no global-only value in it", "openai" not in r.stdout)


def t04_scope_effective():
    d, env = _two_levels()
    r = run(d, env, "config", "--scope", "effective")
    plain = run(d, env, "config")
    cfg = jload(r)
    check("[CP2-T04] --scope effective equals the no-flag output", r.returncode == 0 and r.stdout == plain.stdout, text(r)[:200])
    check("[CP2-T04] project beats global, and untouched keys keep their built-in value",
          bool(cfg) and cfg.get("host_provider") == "google" and cfg.get("related_tests_max") == 3
          and cfg.get("quota_retest_minutes") == 20, str(cfg)[:200])


def t05_sources():
    d, env = project({"host_provider": "google"}, glob={"host_provider": "openai",
                                                        "role_models": {"ratchet-fixer": "sonnet"}})
    src, r = sources(d, env)
    check("[CP2-T05] --sources exits 0", r.returncode == 0, text(r)[:200])
    check("[CP2-T05] host_provider comes from project", src.get("host_provider") == {"project"}, src.get("host_provider"))
    check("[CP2-T05] role_models comes from global", src.get("role_models") == {"global"}, src.get("role_models"))
    check("[CP2-T05] quota_retest_minutes comes from builtin", src.get("quota_retest_minutes") == {"builtin"},
          src.get("quota_retest_minutes"))
    check("[CP2-T05] one entry for every top-level key", set(effective_keys()) <= set(src), sorted(set(effective_keys()) - set(src))[:5])


def t06_sources_merged_map():
    d, env = project({"role_models": {"ratchet-fixer": "haiku"}}, glob={"role_models": {"ratchet-implementer": "opus"}})
    src, r = sources(d, env)
    cfg, _ = effective(d, env)
    check("[CP2-T06] role_models, set at both levels, is reported as project", src.get("role_models") == {"project"}, (src.get("role_models"), text(r)[:150]))
    rm = (cfg or {}).get("role_models") or {}
    check("[CP2-T06] the effective value keeps both entries", rm.get("ratchet-fixer") == "haiku" and rm.get("ratchet-implementer") == "opus", rm)


def t07_set_project():
    d, env = project({})
    gb = gbytes(env)
    r = run(d, env, "config", "--set", "related_tests_max=6")
    check("[CP2-T07] the project file holds related_tests_max 6", r.returncode == 0 and pjson(d).get("related_tests_max") == 6, text(r)[:200])
    check("[CP2-T07] the global file is not created", gbytes(env) == gb and not global_path(env).exists())
    check("[CP2-T07] the output says Set related_tests_max = 6", "Set related_tests_max = 6" in r.stdout, r.stdout[:200])


def t08_set_global():
    d, env = project({})
    pb = pbytes(d)
    r = run(d, env, "config", "--global", "--set", "role_models.ratchet-implementer=opus-x")
    g = json.loads(global_path(env).read_text()) if global_path(env).exists() else None
    check("[CP2-T08] the global file holds the role model", r.returncode == 0 and g == {"role_models": {"ratchet-implementer": "opus-x"}},
          (r.returncode, g, text(r)[:200]))
    check("[CP2-T08] the project file is unchanged", pbytes(d) == pb)
    src, _ = sources(d, env)
    check("[CP2-T08] --sources then reports role_models from global", src.get("role_models") == {"global"}, src.get("role_models"))


def t09_unset_project_reveals_global():
    d, env = project({"host_provider": "google"}, glob={"host_provider": "openai"})
    gb = gbytes(env)
    r = run(d, env, "config", "--unset", "host_provider")
    check("[CP2-T09] the project file no longer has host_provider", r.returncode == 0 and "host_provider" not in pjson(d), text(r)[:200])
    check("[CP2-T09] the global file is untouched", gbytes(env) == gb)
    cfg, _ = effective(d, env)
    src, _ = sources(d, env)
    check("[CP2-T09] the combined view shows openai, sourced from global",
          (cfg or {}).get("host_provider") == "openai" and src.get("host_provider") == {"global"}, (cfg or {}).get("host_provider"))


def t10_unset_global():
    d, env = project({}, glob={"host_provider": "openai"})
    r = run(d, env, "config", "--global", "--unset", "host_provider")
    g = json.loads(global_path(env).read_text()) if global_path(env).exists() else {}
    check("[CP2-T10] the global file no longer has host_provider", r.returncode == 0 and "host_provider" not in g, (r.returncode, text(r)[:200]))
    cfg, _ = effective(d, env)
    src, _ = sources(d, env)
    check("[CP2-T10] the combined view shows the built-in value, sourced from builtin",
          (cfg or {}).get("host_provider") == "auto" and src.get("host_provider") == {"builtin"}, ((cfg or {}).get("host_provider"), src.get("host_provider")))


def t11_unset_nested():
    d, env = project({"provider_share": {"claude": 50, "codex": 30}, "related_tests_max": 4})
    r = run(d, env, "config", "--unset", "provider_share.codex")
    p = pjson(d) if project_path(d).exists() else {}
    check("[CP2-T11] provider_share keeps claude 50 and loses codex", r.returncode == 0 and p.get("provider_share") == {"claude": 50}, (p, text(r)[:200]))
    check("[CP2-T11] other keys are untouched", p.get("related_tests_max") == 4, p)


def t12_unset_missing():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    r = run(d, env, "config", "--unset", "quota_retest_minutes")
    check("[CP2-T12] exit 0 with a message that nothing was set at that level",
          r.returncode == 0 and "nothing" in text(r).lower() and "quota_retest_minutes" in text(r), (r.returncode, text(r)[:200]))
    check("[CP2-T12] the file is unchanged", pbytes(d) == pb)


def t13_set_without_equals():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    for extra in ([], ["--global"]):
        r = run(d, env, "config", *extra, "--set", "related_tests_max")
        tag = "global" if extra else "project"
        check(f"[CP2-T13] {tag}: `--set related_tests_max` exits non-zero saying key=value and naming it",
              r.returncode != 0 and "key=value" in text(r) and "related_tests_max" in text(r), (r.returncode, text(r)[:200]))
    check("[CP2-T13] the project file bytes are unchanged", pbytes(d) == pb)
    check("[CP2-T13] the global file is not created", not global_path(env).exists())


def t14_no_partial_write():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    r = run(d, env, "config", "--set", "related_tests_max=8", "--set", "oops")
    check("[CP2-T14] a call with one bad argument is refused", r.returncode != 0, (r.returncode, text(r)[:200]))
    check("[CP2-T14] related_tests_max is still 4 and the file bytes are unchanged", pbytes(d) == pb and pjson(d).get("related_tests_max") == 4, pbytes(d))


def t15_value_checks_unchanged():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    for label, args, key in (("project", ("--set", "quota_retest_minutes=abc"), "quota_retest_minutes"),
                             ("project", ("--set", "related_test_command=pytest"), "{files}"),
                             ("global", ("--global", "--set", "suite_watch_seconds=0"), "suite_watch_seconds")):
        r = run(d, env, "config", *args)
        check(f"[CP2-T15] {args[-1]} is refused with its message", r.returncode != 0 and key in text(r) and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
    check("[CP2-T15] nothing was written to the project file", pbytes(d) == pb)
    check("[CP2-T15] the global file is not created", not global_path(env).exists())


def t16_global_refusals():
    d, env = project({})
    for args, key in ((("--global", "--set", 'checks=["x"]'), "checks"),
                      (("--global", "--set", 'providers.codex.paid_ok_at="2026-01-01"'), "paid_ok_at")):
        r = run(d, env, "config", *args)
        out = text(r)
        check(f"[CP2-T16] {key}: refused naming the global file and the key",
              r.returncode != 0 and str(global_path(env)) in out and key in out and "Traceback" not in out, (r.returncode, out[:200]))
    check("[CP2-T16] the global file is not created", not global_path(env).exists())


def t17_set_does_not_fold_in_other_levels():
    d, env = project({"related_tests_max": 3}, glob={"host_provider": "openai"})
    r = run(d, env, "config", "--set", "quota_retest_minutes=15")
    check("[CP2-T17] the project file holds exactly the two keys",
          r.returncode == 0 and pjson(d) == {"related_tests_max": 3, "quota_retest_minutes": 15}, (r.returncode, pbytes(d)))


def t18_damaged_global():
    d, env = project({"related_tests_max": 4}, raw_glob="{not json")
    pb, gb = pbytes(d), gbytes(env)
    for args in (("--scope", "global"), ("--global", "--set", "host_provider=x")):
        r = run(d, env, "config", *args)
        check(f"[CP2-T18] `config {' '.join(args)}` exits non-zero naming the global file",
              r.returncode != 0 and str(global_path(env)) in text(r) and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
    check("[CP2-T18] the damaged file is not overwritten", gbytes(env) == gb)
    check("[CP2-T18] the project file is unchanged", pbytes(d) == pb)


def t19_bad_scope():
    d, env = project({})
    r = run(d, env, "config", "--scope", "everywhere")
    out = text(r)
    check("[CP2-T19] an unknown scope is refused listing global, project and effective",
          r.returncode != 0 and all(w in out for w in ("global", "project", "effective")) and "Traceback" not in out, (r.returncode, out[:200]))


def t20_reading_writes_nothing():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    codes = [run(d, env, "config", *a).returncode for a in ((), ("--scope", "global"), ("--sources",))]
    check("[CP2-T20] the three read commands exit 0", codes == [0, 0, 0], codes)
    check("[CP2-T20] no global file appears", not global_path(env).exists())
    check("[CP2-T20] the project file bytes are unchanged", pbytes(d) == pb)


def t21_readme():
    lines = (ROOT / "README.md").read_text().splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"#+\s*Running the tests", l)), None)
    section = []
    for l in lines[(start or 0) + 1:] if start is not None else []:
        if re.match(r"#{1,2}\s", l):
            break
        section.append(l.strip())
    check("[CP2-T21] README's Running the tests section lists python3 tests/test_config_scopes.py",
          "python3 tests/test_config_scopes.py" in section, start)


def t22_inventory():
    own = Path(__file__).read_text()
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    check("[CP2-T22] this file really loads the engine with spec_from_file_location", "spec_from_" + "file_location(" in own)
    check("[CP2-T22] the test inventory lists tests/test_config_scopes.py", "test_config_scopes.py" in inv)


# ---- lane-added cases

def x01_set_without_equals_writes_nothing():
    d, env = project({"related_tests_max": 4})
    pb = pbytes(d)
    r = run(d, env, "config", "--set", "foo")
    check("[CP2-X01] `config --set foo` exits non-zero", r.returncode != 0, (r.returncode, text(r)[:200]))
    check("[CP2-X01] the project file bytes are unchanged", pbytes(d) == pb, pbytes(d))
    check("[CP2-X01] no junk key was written", "foo" not in json.loads(pbytes(d)))
    d2, env2 = project(None)
    r2 = run(d2, env2, "config", "--set", "foo")
    check("[CP2-X01] with no project file, none is created holding a junk key",
          r2.returncode != 0 and (not project_path(d2).exists() or "foo" not in json.loads(project_path(d2).read_text())),
          (r2.returncode, pbytes(d2)))


def x02_no_garbage_key():
    defaults = engine_defaults()
    known = set(defaults["cfg"]) | set(defaults["global_keys"]) | {"agents"}
    p = ROOT / ".ratchet" / "config.json"
    keys = list(json.loads(p.read_text()))
    stray = [k for k in keys if k not in known]
    check("[CP2-X02] every top-level key of this repo's .ratchet/config.json is a known config key", not stray, stray)
    check("[CP2-X02] no key of it looks like JSON text", not [k for k in keys if re.search(r'[{}\[\]":,\s]', k)],
          [k for k in keys if re.search(r'[{}\[\]":,\s]', k)])
    for label, extra in (("project", []), ("global", ["--global"])):
        d, env = project({"related_tests_max": 4})
        pb, gb = pbytes(d), gbytes(env)
        for arg in ('{"strong":"opus","standard":"sonnet"}', '{"a":"b=c"}', '["x"]'):
            r = run(d, env, "config", *extra, "--set", arg)
            check(f"[CP2-X02] {label}: `--set {arg}` is refused", r.returncode != 0, (r.returncode, text(r)[:150]))
        check(f"[CP2-X02] {label}: no key looking like JSON text was written, project file bytes unchanged",
              pbytes(d) == pb and gbytes(env) == gb, (pbytes(d), gbytes(env)))


def x03_unreadable_global_stops_naming_it():
    cases = ["a folder where the file goes", "a file nobody can read", "a per-user folder nobody can enter"]
    if os.geteuid() == 0:
        print("  (skip) CP2-X03 chmod cases: running as root, chmod 000 does not block root; the folder case still runs")
        cases = cases[:1]
    for what in cases:
        d, env = project({"human_review": "end"})
        p = global_path(env)
        p.parent.mkdir(parents=True, exist_ok=True)
        if what.startswith("a folder"):
            p.mkdir()
        else:
            p.write_text("{}")
            (p if "file nobody" in what else p.parent).chmod(0)
        try:
            for cmd in ("status", "models"):
                r = run(d, env, cmd)
                out = text(r)
                check(f"[CP2-X03] {what}, {cmd}: stops non-zero naming the global file, no traceback",
                      r.returncode != 0 and "Traceback" not in out
                      and (str(p) in out or (cmd == "status" and "folder nobody" in what and str(p.parent) in out)),
                      (r.returncode, out[:300]))
        finally:
            p.parent.chmod(0o700)
            if p.exists() and not p.is_dir():
                p.chmod(0o600)


def x04_unset_role_model_levels():
    d, env = project({}, glob={"role_models": {"ratchet-implementer": "opus-g"}})
    r = run(d, env, "config", "--global", "--unset", "role_models.ratchet-implementer")
    g = json.loads(global_path(env).read_text()) if global_path(env).exists() else {}
    cfg, _ = effective(d, env)
    check("[CP2-X04] a global --unset removes the role's entry from the global file",
          r.returncode == 0 and "ratchet-implementer" not in (g.get("role_models") or {}), (r.returncode, g, text(r)[:150]))
    check("[CP2-X04] the role goes back to its shipped default (strong)",
          ((cfg or {}).get("role_models") or {}).get("ratchet-implementer") == "strong", ((cfg or {}).get("role_models") or {}).get("ratchet-implementer"))
    d2, env2 = project({"role_models": {"ratchet-implementer": "haiku-p"}}, glob={"role_models": {"ratchet-implementer": "opus-g"}})
    r2 = run(d2, env2, "config", "--unset", "role_models.ratchet-implementer")
    cfg2, _ = effective(d2, env2)
    check("[CP2-X04] a project --unset reveals the global value",
          r2.returncode == 0 and ((cfg2 or {}).get("role_models") or {}).get("ratchet-implementer") == "opus-g",
          (r2.returncode, ((cfg2 or {}).get("role_models") or {}).get("ratchet-implementer"), text(r2)[:150]))


def x05_invalid_project_value_writes_nothing():
    cases = [("host_models.anthropic=5", "host_models.anthropic"), ("role_models.ratchet-fixer=5", "role_models.ratchet-fixer"),
             ("role_models=5", "role_models"), ("host_models=3", "host_models")]
    for arg, key in cases:
        d, env = project({"related_tests_max": 4})
        pb = pbytes(d)
        r = run(d, env, "config", "--set", arg)
        check(f"[CP2-X05] project `--set {arg}` exits non-zero naming {key}, no traceback",
              r.returncode != 0 and key in text(r) and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
        check(f"[CP2-X05] project `--set {arg}` leaves the file byte-identical", pbytes(d) == pb, pbytes(d))
    # --unset: the candidate that would be written is invalid (another entry already is), so it is refused unchanged
    d, env = project({"role_models": {"ratchet-fixer": 5, "ratchet-implementer": "light"}})
    pb = pbytes(d)
    r = run(d, env, "config", "--unset", "role_models.ratchet-implementer")
    check("[CP2-X05] project `--unset` of a candidate that stays invalid exits non-zero naming the key",
          r.returncode != 0 and "role_models.ratchet-fixer" in text(r) and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
    check("[CP2-X05] project `--unset` leaves the file byte-identical", pbytes(d) == pb, pbytes(d))
    # a valid project set still works, and a project provider may keep its paid consent
    d, env = project({"providers": [{"id": "p", "preset": "codex", "paid_ok_at": "2026-01-01T00:00Z"}]})
    r = run(d, env, "config", "--set", "role_models.ratchet-fixer=light")
    check("[CP2-X05] a valid project --set still writes, and keeps a project provider's paid consent",
          r.returncode == 0 and pjson(d).get("providers", [{}])[0].get("paid_ok_at") and pjson(d)["role_models"]["ratchet-fixer"] == "light",
          (r.returncode, text(r)[:200]))


def x06_unset_success_only_after_write():
    base = {"related_tests_max": 4, "role_models": {"ratchet-implementer": "light"}}
    refused = [("a refused --set", ["--set", "foo"], base),
               ("a candidate that fails validation", ["--set", "role_models.ratchet-fixer=5"], base),
               ("a still-invalid candidate", ["--set", "related_tests_max=5"],
                {**base, "role_models": {"ratchet-fixer": 5, "ratchet-implementer": "light"}})]
    for what, extra, cfg0 in refused:
        d, env = project(cfg0)
        pb = pbytes(d)
        r = run(d, env, "config", "--unset", "role_models.ratchet-implementer", *extra)
        out = text(r)
        check(f"[CP2-X06] --unset with {what} exits non-zero", r.returncode != 0, (r.returncode, out[:200]))
        check(f"[CP2-X06] --unset with {what} leaves the file byte-identical", pbytes(d) == pb, pbytes(d))
        check(f"[CP2-X06] --unset with {what} prints no Removed/Set success line",
              "Removed" not in out and "Set " not in out, out[:300])
    d, env = project(base)
    r = run(d, env, "config", "--unset", "role_models.ratchet-implementer")
    check("[CP2-X06] a normal --unset prints Removed, after the file was written",
          r.returncode == 0 and "Removed role_models.ratchet-implementer" in text(r)
          and "ratchet-implementer" not in (pjson(d).get("role_models") or {}), (r.returncode, text(r)[:200]))


# One row per refused path of `config --set/--unset/--global`: (label, project file, global file or None, args).
# A new refused path is one row; every row is also run with an --unset of an absent key and of a present key.
_X07_BASE = {"related_tests_max": 4}
_X07_ROWS = [
    ("--set with no '='", _X07_BASE, None, ["--set", "foo"]),
    ("--global --set with no '='", _X07_BASE, None, ["--global", "--set", "foo"]),
    ("--set with an empty key", _X07_BASE, None, ["--set", "=1"]),
    ("--set with a non-plain key", _X07_BASE, None, ["--set", "bad key=1"]),
    ("--set with an empty dotted part", _X07_BASE, None, ["--set", "a..b=1"]),
    ("--global --set of a non-plain key", _X07_BASE, None, ["--global", "--set", "bad key=1"]),
    ("--global --set of a key off the allow-list", _X07_BASE, None, ["--global", "--set", 'checks=["x"]']),
    ("--global --set paid_ok_at", _X07_BASE, None, ["--global", "--set", 'providers.codex.paid_ok_at="2026-01-01"']),
    ("--global --set with a bad shape", _X07_BASE, None, ["--global", "--set", "host_models=3"]),
    ("--global --set with a bad nested shape", _X07_BASE, {"host_provider": "openai"},
     ["--global", "--set", "role_models.ratchet-fixer=5"]),
    ("project --set with a bad shape", _X07_BASE, None, ["--set", "role_models.ratchet-fixer=5"]),
    ("project --set of a whole group with a bad shape", _X07_BASE, None, ["--set", "host_models=3"]),
    ("project --set with a bad value", _X07_BASE, None, ["--set", "quota_retest_minutes=abc"]),
    ("project --set while another entry stays invalid",
     {"role_models": {"ratchet-fixer": 5, "ratchet-implementer": "light"}}, None, ["--set", "related_tests_max=5"]),
    ("--set into a non-group", {"host_provider": "google"}, None, ["--set", "host_provider.x=1"]),
]


def x07_every_refused_config_write_is_silent_and_inert():
    cases = [(f"{label}", proj, glob, args) for label, proj, glob, args in _X07_ROWS]
    # an --unset whose removal leaves another invalid entry is refused on its own
    cases.append(("--unset leaving another invalid entry", {"role_models": {"ratchet-fixer": 5, "ratchet-implementer": "light"}},
                  None, ["--unset", "role_models.ratchet-implementer"]))
    for label, proj, glob, args in list(cases):
        if args[0] == "--unset":
            continue
        for unset in ("quota_retest_minutes", "related_tests_max"):  # absent key, then a present one
            cases.append((f"--unset {unset} + {label}", proj, glob, ["--unset", unset, *args]))
    for label, proj, glob, args in cases:
        d, env = project(proj, glob=glob)
        pb, gb = pbytes(d), gbytes(env)
        folder = global_path(env).parent
        folder_before = folder.exists()
        r = run(d, env, "config", *args)
        out = text(r)
        check(f"[CP2-X07] {label}: exits non-zero without a traceback", r.returncode != 0 and "Traceback" not in out,
              (r.returncode, out[:200]))
        check(f"[CP2-X07] {label}: the project file is byte-identical", pbytes(d) == pb, pbytes(d))
        check(f"[CP2-X07] {label}: the global file is byte-identical (or still absent)", gbytes(env) == gb, gbytes(env))
        check(f"[CP2-X07] {label}: no per-user folder was created", folder.exists() == folder_before)
        check(f"[CP2-X07] {label}: no Removed/Set/Unset success text and no 'nothing to remove' claim",
              not re.search(r"✓|Removed |Set \S+ = |Unset|nothing to remove", out), out[:300])


def x08_project_file_of_the_wrong_top_level_shape():
    shapes = ["[]", '"x"', "5", "null", '[{"a": 1}]']
    invocations = [["--set", "related_tests_max=6"], ["--unset", "related_tests_max"], ["--scope", "project"],
                   ["--scope", "effective"], ["--sources"]]
    for raw in shapes:
        for args in invocations:
            d, env = project({})
            project_path(d).write_text(raw)
            pb = pbytes(d)
            r = run(d, env, "config", *args)
            out = text(r)
            label = f"{raw} with config {' '.join(args)}"
            check(f"[CP2-X08] {label}: exits non-zero without a traceback", r.returncode != 0 and "Traceback" not in out,
                  (r.returncode, out[:200]))
            check(f"[CP2-X08] {label}: names the file and says it must be a JSON object",
                  str(project_path(d)) in out and "JSON object" in out, out[:300])
            check(f"[CP2-X08] {label}: prints no settings and the file is byte-identical",
                  pbytes(d) == pb and not r.stdout.strip().startswith(("{", "[")), out[:200])
    d, env = project({})
    project_path(d).unlink()
    r = run(d, env, "config", "--scope", "project")
    check("[CP2-X08] a missing project file still shows empty project settings", r.returncode == 0 and jload(r) == {},
          (r.returncode, text(r)[:200]))


# The settings left unchecked on purpose; the same list is in the docstring of Ctx.validate_candidate.
X09_UNCHECKED = {"related_tests_max", "load_stretch_max", "min_review_timeout_sec", "design_md"}
# Refused by their own older, pinned messages (the key is named; the file is not).
X09_OWN_MESSAGE = {"quota_retest_minutes", "provider_share", "related_test_command", "suite_watch_seconds"}


def x09_every_setting_is_validated_or_listed_unchecked():
    defaults = engine_defaults()["cfg"]
    doc = importlib.util.spec_from_file_location("ratchet_x09", ENGINE_PATH)
    mod = importlib.util.module_from_spec(doc)
    doc.loader.exec_module(mod)
    text_doc = mod.Ctx.validate_candidate.__doc__
    listed = set(re.findall(r"^\s+(\w+) {2,}\S", text_doc.split("Deliberately left unchecked", 1)[1], re.M))
    check("[CP2-X09] the test's unchecked list matches the function's docstring list", listed == X09_UNCHECKED,
          (sorted(listed), sorted(X09_UNCHECKED)))
    for key, base in defaults.items():
        wrong = '"x"' if isinstance(base, list) else "[1]"  # a type no consumer of that setting accepts
        d, env = project({})
        pb = pbytes(d)
        r = run(d, env, "config", "--set", f"{key}={wrong}")
        out = text(r)
        if key in X09_UNCHECKED:
            check(f"[CP2-X09] {key} is on the documented unchecked list", key in listed, key)
            continue
        check(f"[CP2-X09] {key}: a wrong-typed value is refused naming the key and the file, without a traceback",
              r.returncode != 0 and key in out and "Traceback" not in out
              and (key in X09_OWN_MESSAGE or str(project_path(d)) in out),
              (r.returncode, out[:250]))
        check(f"[CP2-X09] {key}: the refused write left the project file byte-identical", pbytes(d) == pb, out[:200])
        d, env = project({})
        r = run(d, env, "config", "--set", f"{key}={json.dumps(base)}")
        check(f"[CP2-X09] {key}: its own shipped value still writes fine", r.returncode == 0, text(r)[:250])
    d, env = project({})
    for kv in ("probe_ttl_minutes=1.5", 'converge_after_round="3"', "spec_keep=true", "regression_command=null"):
        r = run(d, env, "config", "--set", kv)
        check(f"[CP2-X09] {kv} (accepted by the engine today) still writes fine", r.returncode == 0, text(r)[:250])
    repo_cfg = json.loads((ROOT / ".ratchet" / "config.json").read_text())
    d, env = project(repo_cfg)
    r = run(d, env, "config")
    check("[CP2-X09] the repo's own config.json still loads with the same `config` output",
          r.returncode == 0 and r.stdout == json.dumps(cl.old_merge(repo_cfg), indent=2) + "\n", text(r)[:250])
    for scope_args in (["--scope", "project"], ["--sources"]):  # the read paths refuse a damaged layer the same way
        d, env = project({"max_ui_rounds": "many"})
        r = run(d, env, "config", *scope_args)
        check(f"[CP2-X09] config {' '.join(scope_args)} refuses a damaged layer naming key and file",
              r.returncode != 0 and "max_ui_rounds" in text(r) and str(project_path(d)) in text(r), text(r)[:250])


# Int-default settings a consumer reads through int()/float() (documented as coerced in the docstring), and the
# duration settings that also accept a fraction (the docstring's "Whole numbers only" table).
X10_COERCED = {"converge_after_round", "checkin_round", "probe_timeout_sec", "probe_ttl_minutes", "unavailable_ttl_hours"}
X10_FRACTIONAL = {"test_timeout_sec", "min_review_seconds", "suite_watch_seconds", "quota_retest_minutes"}


def x10_int_default_settings_take_whole_numbers():
    defaults = engine_defaults()["cfg"]
    doc = importlib.util.spec_from_file_location("ratchet_x10", ENGINE_PATH)
    mod = importlib.util.module_from_spec(doc)
    doc.loader.exec_module(mod)
    text_doc = mod.Ctx.validate_candidate.__doc__
    table = text_doc.split("Whole numbers only", 1)[1].split("Deliberately left unchecked", 1)[0]
    listed = set(re.findall(r"^\s+(\w+) {2,}\S", table, re.M))
    check("[CP2-X10] the test's fractional list matches the function's docstring table", listed == X10_FRACTIONAL,
          (sorted(listed), sorted(X10_FRACTIONAL)))
    coerced_doc = text_doc.split("Numbers that a consumer coerces", 1)[0]
    for key in X10_COERCED:
        check(f"[CP2-X10] {key} is named in the docstring's coerced group", key in coerced_doc, key)
    keys = [k for k, v in defaults.items() if isinstance(v, int) and not isinstance(v, bool)]
    check("[CP2-X10] there are int-default settings to check", len(keys) >= 10, keys)
    for key in keys:
        if key in X09_UNCHECKED or key in X10_COERCED or key in X10_FRACTIONAL:
            continue
        d, env = project({})
        pb = pbytes(d)
        r = run(d, env, "config", "--set", f"{key}=1.5")
        out = text(r)
        check(f"[CP2-X10] {key}=1.5 is refused naming the key and the file", r.returncode != 0 and key in out
              and str(project_path(d)) in out and "Traceback" not in out, (r.returncode, out[:250]))
        check(f"[CP2-X10] {key}: the refused write left the project file byte-identical", pbytes(d) == pb, out[:200])
        for good in (defaults[key], float(defaults[key]) + 1, int(defaults[key]) + 1):
            d, env = project({})
            r = run(d, env, "config", "--set", f"{key}={json.dumps(good)}")
            check(f"[CP2-X10] {key}={good} (a whole number) still writes", r.returncode == 0, text(r)[:250])
    for key in ("test_timeout_sec", "min_review_seconds", "suite_watch_seconds"):
        d, env = project({})
        r = run(d, env, "config", "--set", f"{key}=1.5")
        check(f"[CP2-X10] {key}=1.5 (a duration) still writes", r.returncode == 0, text(r)[:250])


TESTS = [(f"CP2-T{n:02d}", fn) for n, fn in enumerate(
    [t01_no_flags_unchanged, t02_scope_global, t03_scope_project, t04_scope_effective, t05_sources,
     t06_sources_merged_map, t07_set_project, t08_set_global, t09_unset_project_reveals_global, t10_unset_global,
     t11_unset_nested, t12_unset_missing, t13_set_without_equals, t14_no_partial_write, t15_value_checks_unchanged,
     t16_global_refusals, t17_set_does_not_fold_in_other_levels, t18_damaged_global, t19_bad_scope,
     t20_reading_writes_nothing, t21_readme, t22_inventory], 1)] + [
    ("CP2-X01", x01_set_without_equals_writes_nothing), ("CP2-X02", x02_no_garbage_key),
    ("CP2-X03", x03_unreadable_global_stops_naming_it), ("CP2-X04", x04_unset_role_model_levels),
    ("CP2-X05", x05_invalid_project_value_writes_nothing), ("CP2-X06", x06_unset_success_only_after_write),
    ("CP2-X07", x07_every_refused_config_write_is_silent_and_inert),
    ("CP2-X08", x08_project_file_of_the_wrong_top_level_shape),
    ("CP2-X09", x09_every_setting_is_validated_or_listed_unchecked),
    ("CP2-X10", x10_int_default_settings_take_whole_numbers)]


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
