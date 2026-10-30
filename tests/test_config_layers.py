#!/usr/bin/env python3
"""
CP1 tests: a machine-wide config file under the project's.   python3 tests/test_config_layers.py

Ratchet reads <RATCHET_HOME>/config.json (the "global" file) and lays the project's .ratchet/config.json over
it, key by key (a dict value merges one level deep, anything else replaces whole). With no global file every
command behaves exactly as before. A damaged global file, paid consent (providers[].paid_ok_at) in it, or a
project-only setting in it stops the command naming the file and the key. A locked setting changed in the global
file during an approved run asks for approval like a project-file change.

Everything is driven through the engine's own CLI entry point, in-process (standard 30): the engine module is
loaded once per process by tests/trust_helpers.py, and each case builds its own throwaway git project and its own
private RATCHET_HOME. No network, no real reviewer CLI.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

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

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP1-[TX]\d+[a-z]?)\]")

RESET_GLOBALS = {"_IN_HOOK": False, "_OVERRIDE_CONFIRMED": False, "_CHAIN_REBUILD": False,
                 "_HOOK_ENV_ADVISORY": False, "_STATE_LOCK_HELD": False, "_ENGINE_ARGV": None}
LOCK_WAIT_CAP = 0.3


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


# ------------------------------------------------------------------------------------------------ fixtures

def engine():
    return th.cp5y_load_engine()


def run(d, env, *args, stdin=None):
    """One in-process engine call; returns an object with returncode / stdout / stderr."""
    m = engine()
    with th.InProcessCall(m, d, env, RESET_GLOBALS, LOCK_WAIT_CAP, stdin=stdin) as r:
        m.main(list(args))
    return r


def text(r):
    return r.stdout + r.stderr


def global_path(env):
    return Path(env["RATCHET_HOME"]) / "config.json"


def put_global(env, data=None, raw=None):
    p = global_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else json.dumps(data))
    return p


def project_path(d):
    return Path(d) / ".ratchet" / "config.json"


def project(proj_cfg=None, glob=None, raw_glob=None):
    """A throwaway git project (optional project config) plus a private env (own HOME and RATCHET_HOME, which
    holds the global file when `glob`/`raw_glob` is given)."""
    env = th.make_env()
    d = Path(_tmp.mkdtemp(prefix="ratchet-cfglayers-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    (d / ".ratchet").mkdir()
    if proj_cfg is not None:
        project_path(d).write_text(json.dumps(proj_cfg))
    r = run(d, env, "init", "feature", "--slug", "f")  # an active run, so `status` has something to show
    assert r.returncode == 0, text(r)
    if glob is not None or raw_glob is not None:
        put_global(env, glob, raw_glob)
    return d, env


def effective(d, env):
    r = run(d, env, "config")
    try:
        return json.loads(r.stdout), r
    except Exception:
        return None, r


def old_merge(user):
    """The pre-change result: shipped defaults with the project file on top (the old config_from_user rule)."""
    m = engine()
    cfg = json.loads(json.dumps(m.DEFAULT_CONFIG))
    if user.get("review_depth") == m.OLD_DEFAULT_REVIEW_DEPTH:
        user = {k: v for k, v in user.items() if k != "review_depth"}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def approved_project(glob_before=None):
    """A throwaway project with one checkpoint approved into building (in-process mirror of
    trust_helpers.cp3_project). `glob_before` is written to the global file before the plan is approved."""
    env = th.make_env()
    if glob_before is not None:
        put_global(env, glob_before)
    d = Path(_tmp.mkdtemp(prefix="ratchet-cfglayers-ap-"))
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
    run(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps(
        {"cwd": str(d), "prompt": "please build the feature", "session_id": "s-owner", "transcript_path": str(tp)}))
    r = run(d, env, "init", "feature", "--slug", "f")
    assert r.returncode == 0, text(r)
    run(d, env, "config", "--set", "min_review_seconds=0", "providers=[]")
    rd = d / ".ratchet/runs/f"
    (rd / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN,
                                                      "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = run(d, env, "plan-submit")
    assert r.returncode == 0, text(r)
    run(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    run(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps(
        {"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner", "transcript_path": str(tp)}))
    r = run(d, env, "approve-plan")
    assert r.returncode == 0, text(r)
    th.cp3_write_tests(d)
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    return d, env


INTEGRITY_MARK = "changed after the user approved the plan"


# ------------------------------------------------------------------------------------------------ cases

def t01_no_global_file_unchanged():
    proj = {"reviewers": 2, "role_models": {"ratchet-implementer": "sonnet"}}
    d, env = project(proj)
    cfg, r = effective(d, env)
    check("[CP1-T01] with a project config and no global file the settings equal the old result key for key",
          cfg == old_merge(proj), text(r)[:200])
    check("[CP1-T01] no global file is created by loading", not global_path(env).exists())
    d2, env2 = project(None)
    cfg2, r2 = effective(d2, env2)
    check("[CP1-T01] with no project config either, the settings equal the shipped defaults",
          cfg2 == old_merge({}), text(r2)[:200])
    check("[CP1-T01] still no global file after the second load", not global_path(env2).exists())


def t02_global_model_applies():
    d, env = project({"human_review": "end"}, glob={"role_models": {"ratchet-implementer": "opus-x"}})
    cfg, r = effective(d, env)
    base = old_merge({})["role_models"]
    want = dict(base, **{"ratchet-implementer": "opus-x"})
    check("[CP1-T02] the global role_models.ratchet-implementer applies and every other role keeps its default",
          cfg is not None and cfg["role_models"] == want, text(r)[:300])


def t03_project_beats_global():
    d, env = project({"role_models": {"ratchet-implementer": "sonnet-z"}},
                     glob={"role_models": {"ratchet-implementer": "opus-x", "ratchet-adversary": "haiku-y"}})
    cfg, r = effective(d, env)
    want = dict(old_merge({})["role_models"], **{"ratchet-implementer": "sonnet-z", "ratchet-adversary": "haiku-y"})
    check("[CP1-T03] the project's value wins, the global-only key survives, defaults fill the rest",
          cfg is not None and cfg["role_models"] == want, text(r)[:300])


def t04_non_dict_replaces_whole():
    glob = {"host_provider": "codex", "providers": [{"id": "g1", "preset": "codex"}, {"id": "g2", "preset": "gemini"}]}
    d, env = project({"host_provider": "claude", "providers": []}, glob=glob)
    cfg, r = effective(d, env)
    check("[CP1-T04] the project's scalar and empty list replace the global ones; nothing appended or averaged",
          cfg is not None and cfg["host_provider"] == "claude" and cfg["providers"] == [], text(r)[:300])
    d2, env2 = project({"human_review": "end"}, glob=glob)
    cfg2, r2 = effective(d2, env2)
    check("[CP1-T04] with the project silent the global scalar and list apply whole",
          cfg2 is not None and cfg2["host_provider"] == "codex" and cfg2["providers"] == glob["providers"], text(r2)[:300])


def t05_old_default_review_depth_dropped_per_level():
    m = engine()
    old = json.loads(json.dumps(m.OLD_DEFAULT_REVIEW_DEPTH))
    new_default = json.loads(json.dumps(m.DEFAULT_CONFIG["review_depth"]))
    d1 = {"low": ["any-both"], "normal": ["any-both"], "high": ["any-both", "any-both"]}
    d2 = {"low": ["any-correctness"], "normal": ["any-standards"], "high": ["any-correctness"]}
    check("[CP1-T05] the fixture depths are really different from the old and new defaults",
          d1 != old and d2 != old and d1 != new_default and d2 != new_default and d1 != d2)
    d, env = project({"review_depth": old}, glob={"role_models": {"ratchet-implementer": "opus-x"}})
    cfg, r = effective(d, env)
    check("[CP1-T05] an old default in the project, global silent on depth: the new default applies",
          cfg is not None and cfg["review_depth"] == new_default, text(r)[:200])
    d, env = project({"review_depth": d1})
    cfg, r = effective(d, env)
    check("[CP1-T05] a real project depth applies", cfg is not None and cfg["review_depth"] == d1, text(r)[:200])
    d, env = project({"review_depth": d2}, glob={"role_models": {"ratchet-implementer": "opus-x"}})
    cfg, r = effective(d, env)
    check("[CP1-T05] a global file without depth leaves the project's real depth alone",
          cfg is not None and cfg["review_depth"] == d2, text(r)[:200])
    d, env = project({"review_depth": d2}, glob={"review_depth": d1})
    r = run(d, env, "status")
    check("[CP1-T05] review_depth is project-only: in the global file it stops the command naming file and key",
          r.returncode != 0 and str(global_path(env)) in text(r) and "review_depth" in text(r), text(r)[:300])


def t06_damaged_global_stops():
    d, env = project({"human_review": "end"}, raw_glob="{ this is not json")
    r = run(d, env, "status")
    out = text(r)
    check("[CP1-T06] a damaged global file stops the command (non-zero exit)", r.returncode != 0, out[:200])
    check("[CP1-T06] the message names the global file path", str(global_path(env)) in out, out[:300])
    check("[CP1-T06] the message does not blame the project file", str(project_path(d)) not in out, out[:300])


def t07_global_not_an_object():
    d, env = project({"human_review": "end"}, raw_glob=json.dumps(["not", "an", "object"]))
    r = run(d, env, "status")
    out = text(r)
    check("[CP1-T07] a global file holding a JSON list stops the command", r.returncode != 0, out[:200])
    check("[CP1-T07] the message names the global file", str(global_path(env)) in out, out[:300])
    check("[CP1-T07] the message says an object is expected", "object" in out.lower(), out[:300])


PAID = {"id": "paidy", "preset": "codex", "cost": "per-use", "paid_ok_at": "2026-01-01T00:00:00+00:00"}


def t08_paid_consent_refused_in_global():
    d, env = project({"human_review": "end"}, glob={"providers": [PAID]})
    r = run(d, env, "status")
    out = text(r)
    check("[CP1-T08] paid consent in the global file stops the command", r.returncode != 0, out[:200])
    check("[CP1-T08] the message names the global file", str(global_path(env)) in out, out[:300])
    check("[CP1-T08] the message names paid_ok_at", "paid_ok_at" in out, out[:300])
    d2, env2 = project({"providers": [PAID]})
    r2 = run(d2, env2, "status")
    check("[CP1-T08] the same field in the project file still loads as before", r2.returncode == 0, text(r2)[:300])
    cfg, _ = effective(d2, env2)
    check("[CP1-T08] and stays in the effective providers", cfg is not None and cfg["providers"] == [PAID])


PROJECT_ONLY = {
    "checks": [{"kind": "k", "command": "true", "title": "t", "added": "2026-01-01"}],
    "regression_command": "python3 -c pass",
    "related_test_command": "python3 -c pass {files}",
    "doc_test_command": "python3 -c pass",
    "app": {"start_command": None, "url": None, "healthcheck": None},
    "lane_start_command": "true",
}


def t09_project_only_keys_refused_in_global():
    for key, val in PROJECT_ONLY.items():
        d, env = project({"human_review": "end"}, glob={key: val})
        r = run(d, env, "status")
        out = text(r)
        check(f"[CP1-T09] {key} in the global file stops the command", r.returncode != 0, out[:200])
        check(f"[CP1-T09] {key}: the message names the global file", str(global_path(env)) in out, out[:300])
        check(f"[CP1-T09] {key}: the message names the key", key in out, out[:300])
        d2, env2 = project({key: val})
        r2 = run(d2, env2, "status")
        check(f"[CP1-T09] {key} in the project file still loads", r2.returncode == 0, text(r2)[:300])


def t10_machine_wide_keys_load():
    glob = {"role_models": {"ratchet-implementer": "opus-x"},
            "providers": [{"id": "codex", "preset": "codex"}],
            "host_provider": "codex", "ui_reviewers": {"visual": ["codex"]}, "agents": {"fixer": "sonnet-9"},
            "host_models": {"anthropic": {"standard": "sonnet-9"}}}
    d, env = project({"human_review": "end"}, glob=glob)
    r = run(d, env, "status")
    check("[CP1-T10] a global file of machine-wide settings loads without error", r.returncode == 0, text(r)[:300])
    cfg, r2 = effective(d, env)
    good = cfg is not None
    check("[CP1-T10] role_models from the global file show",
          good and cfg["role_models"]["ratchet-implementer"] == "opus-x")
    check("[CP1-T10] providers from the global file show", good and cfg["providers"] == glob["providers"])
    check("[CP1-T10] host_provider, ui_reviewers and agents from the global file load",
          good and cfg["host_provider"] == "codex" and cfg["ui_reviewers"]["visual"] == ["codex"]
          and cfg["agents"] == {"fixer": "sonnet-9"})
    check("[CP1-T10] host_models from the global file merge one level deep; a sub-entry replaces the shipped one whole",
          good and cfg["host_models"]["anthropic"] == {"standard": "sonnet-9"}
          and set(cfg["host_models"]) >= set(old_merge({})["host_models"]))


def t11_writers_never_copy_global_values():
    glob = {"role_models": {"ratchet-implementer": "opus-x"}, "providers": [{"id": "codex", "preset": "codex"}]}
    d, env = project({"human_review": "end"}, glob=glob)
    cfg, _ = effective(d, env)
    check("[CP1-T11] the global values are in effect while the writers run",
          cfg is not None and cfg["role_models"]["ratchet-implementer"] == "opus-x")
    r = run(d, env, "config", "--set", "min_review_seconds=5")
    check("[CP1-T11] config --set succeeds", r.returncode == 0, text(r)[:300])
    on_disk = json.loads(project_path(d).read_text())
    check("[CP1-T11] after config --set the project file holds only its own keys plus the change",
          on_disk == {"human_review": "end", "min_review_seconds": 5}, on_disk)
    r = run(d, env, "spec", "keep", "on")
    on_disk = json.loads(project_path(d).read_text())
    check("[CP1-T11] after spec keep the project file holds only its own keys plus the change",
          on_disk == {"human_review": "end", "min_review_seconds": 5, "spec_keep": True}, (on_disk, text(r)[:200]))
    check("[CP1-T11] the global file is left untouched", json.loads(global_path(env).read_text()) == glob)


def t12_locked_setting_in_global_asks_approval():
    d, env = approved_project()
    put_global(env, {"role_models": {"ratchet-implementer": "opus-x"}})
    r = run(d, env, "tests-written", "CP1")
    out = text(r)
    check("[CP1-T12] a locked setting changed in the global file stops the gated command", r.returncode != 0, out[:300])
    check("[CP1-T12] it says the setting changed after approval", INTEGRITY_MARK in out, out[:400])
    check("[CP1-T12] it names the changed rule", "role_models" in out, out[:400])
    check("[CP1-T12] it asks for approval like a project-file change", "approve-changes" in out, out[:400])
    d2, env2 = approved_project(glob_before={"role_models": {"ratchet-implementer": "opus-x"}})
    r2 = run(d2, env2, "tests-written", "CP1")
    out2 = text(r2)
    check("[CP1-T12] a global file that did not change since approval does not trigger it",
          r2.returncode == 0 and INTEGRITY_MARK not in out2, out2[:400])


def t13_unlocked_global_keys_do_not_disturb_run():
    d, env = approved_project()
    put_global(env, {"providers": [{"id": "codex", "preset": "codex"}]})
    own = json.loads(project_path(d).read_text())
    own.pop("providers", None)  # the project's empty providers list would otherwise shadow the global one
    project_path(d).write_text(json.dumps(own))
    cfg, _ = effective(d, env)
    check("[CP1-T13] the unlocked global key is in effect", cfg is not None and {"id": "codex", "preset": "codex"} in cfg["providers"])
    r = run(d, env, "tests-written", "CP1")
    out = text(r)
    check("[CP1-T13] no approval is requested and the gated command proceeds",
          r.returncode == 0 and INTEGRITY_MARK not in out, out[:400])


def t14_only_the_global_file_in_use_counts():
    d, env_a = project({"human_review": "end"}, glob={"role_models": {"ratchet-implementer": "opus-x"}})
    env_b = th.make_env()
    cfg_a, _ = effective(d, env_a)
    cfg_b, _ = effective(d, env_b)
    check("[CP1-T14] the folder with a global file changes the result",
          cfg_a is not None and cfg_a["role_models"]["ratchet-implementer"] == "opus-x")
    check("[CP1-T14] the empty folder does not (it equals the old result)", cfg_b == old_merge({"human_review": "end"}))
    # A file at the default location (HOME/.config/ratchet) is ignored when RATCHET_HOME points elsewhere.
    env_c = th.make_env()
    stray = Path(env_c["HOME"]) / ".config" / "ratchet" / "config.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text(json.dumps({"role_models": {"ratchet-implementer": "stray-model"}}))
    cfg_c, _ = effective(d, env_c)
    check("[CP1-T14] a file in the home folder is not read when RATCHET_HOME points elsewhere",
          cfg_c == old_merge({"human_review": "end"}))
    env_d = {k: v for k, v in env_c.items() if k != "RATCHET_HOME"}
    cfg_d, _ = effective(d, env_d)
    check("[CP1-T14] with RATCHET_HOME unset the file under HOME/.config/ratchet is the global file",
          cfg_d is not None and cfg_d["role_models"]["ratchet-implementer"] == "stray-model")


def t15_readme_lists_the_test_file():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("### Running the tests", 1)[-1]
    check("[CP1-T15] the README 'Running the tests' section lists python3 tests/test_config_layers.py",
          re.search(r"^python3 tests/test_config_layers\.py$", section, re.M) is not None)


def t16_inventory_lists_the_file_when_it_scans_engine_source():
    src = Path(__file__).read_text()
    # Patterns assembled from pieces so this file's own source never matches them by accident.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(src) or src_pat.search(src))
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    listed = "test_config_layers.py" in inv
    check("[CP1-T16] the file is in the test inventory exactly when it reads engine source or loads it by spec",
          (not scans) or listed, f"scans={scans} listed={listed}")


def x01_provider_writers_never_copy_global_list():
    glob = {"providers": [{"id": "codex", "preset": "codex"}]}
    d, env = project({"human_review": "end"}, glob=glob)
    r = run(d, env, "providers", "--remove", "nonexistent")
    check("[CP1-X01] providers --remove on a project with no list of its own is refused, naming the global file",
          r.returncode != 0 and str(global_path(env)) in text(r), text(r)[:300])
    check("[CP1-X01] the project file is left as it was",
          json.loads(project_path(d).read_text()) == {"human_review": "end"})
    d2, env2 = project({"human_review": "end", "providers": [{"id": "gemini", "preset": "gemini"}]}, glob=glob)
    r2 = run(d2, env2, "providers", "--remove", "nonexistent")
    check("[CP1-X01] with a list of its own the project keeps writing it", r2.returncode == 0, text(r2)[:300])
    check("[CP1-X01] and the global entry is not copied in",
          json.loads(project_path(d2).read_text())["providers"] == [{"id": "gemini", "preset": "gemini"}])


def x03_project_sub_entry_replaces_whole():
    d, env = project({"host_models": {"anthropic": {"standard": "x"}}},
                     glob={"host_models": {"anthropic": {"standard": "g"}}})
    cfg, _ = effective(d, env)
    check("[CP1-X03] a partial project sub-entry replaces the shipped and global sub-entries whole (neither fills in)",
          cfg is not None and cfg["host_models"]["anthropic"] == {"standard": "x"}, cfg and cfg["host_models"])


def fresh_project(glob=None, raw_glob=None):
    """A git project with NO .ratchet/config.json yet and a private env; the global file is written first."""
    env = th.make_env()
    d = Path(_tmp.mkdtemp(prefix="ratchet-cfglayers-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    if glob is not None or raw_glob is not None:
        put_global(env, glob, raw_glob)
    return d, env


def x04_init_does_not_hide_global_values():
    glob = {"ui_reviewers": {"visual": ["codex"]}, "host_provider": "codex"}
    d, env = fresh_project(glob)
    r = run(d, env, "init", "feature", "--slug", "f")
    check("[CP1-X04] init succeeds with a global file", r.returncode == 0, text(r)[:300])
    on_disk = json.loads(project_path(d).read_text())
    check("[CP1-X04] the project file holds none of the keys the global file sets",
          not any(k in on_disk for k in glob), on_disk)
    cfg, _ = effective(d, env)
    check("[CP1-X04] the global values are in effect after init",
          cfg is not None and cfg["ui_reviewers"]["visual"] == ["codex"] and cfg["host_provider"] == "codex", cfg)
    check("[CP1-X04] the keys the global file does not set are still written",
          "regression_command" in on_disk and "app" in on_disk and "human_review" in on_disk, on_disk)
    d2, env2 = fresh_project()
    r2 = run(d2, env2, "init", "feature", "--slug", "f")
    keys = ["human_review", "host_provider", "ui_reviewers", "regression_command", "commit_per_checkpoint",
            "design_md", "standards", "app"]
    check("[CP1-X04] with no global file the project file is exactly the shipped defaults",
          r2.returncode == 0 and json.loads(project_path(d2).read_text())
          == {k: engine().DEFAULT_CONFIG[k] for k in keys}, text(r2)[:300])
    check("[CP1-X04] and init still advises providers --discover", "providers --discover" in r2.stdout, r2.stdout)
    d3, env3 = fresh_project({"providers": [{"id": "codex", "preset": "codex"}]})
    r3 = run(d3, env3, "init", "feature", "--slug", "f")
    check("[CP1-X04] with a global providers list init does not advise the refused providers --discover",
          r3.returncode == 0 and "providers --discover" not in r3.stdout and str(global_path(env3)) in r3.stdout,
          r3.stdout)


def x05_global_shapes_are_validated():
    for name, bad, key in (("providers is a number", {"providers": 5}, "providers"),
                           ("providers holds a scalar", {"providers": [1]}, "providers[0]"),
                           ("role_models is a list", {"role_models": ["x"]}, "role_models")):
        d, env = project({"human_review": "end"}, glob=bad)
        r = run(d, env, "status")
        out = text(r)
        check(f"[CP1-X05] {name}: the command stops naming the file and the setting",
              r.returncode != 0 and str(global_path(env)) in out and key in out and "Traceback" not in out, out[:300])


def x06_damaged_global_stops_before_any_write():
    d, env = project({"human_review": "end"}, glob={"checks": []})
    r = run(d, env, "config", "--set", "min_review_seconds=5")
    check("[CP1-X06] config --set with a damaged global file fails naming it",
          r.returncode != 0 and str(global_path(env)) in text(r), text(r)[:300])
    check("[CP1-X06] and the project file is left as it was",
          json.loads(project_path(d).read_text()) == {"human_review": "end"})
    d2, env2 = fresh_project(raw_glob="{not json")
    r2 = run(d2, env2, "init", "feature", "--slug", "f")
    check("[CP1-X06] init with a damaged global file fails and writes no project file",
          r2.returncode != 0 and not project_path(d2).exists(), text(r2)[:300])


def x08_user_config_matches_config_from_user_without_global():
    m = engine()
    d, env = project({"human_review": "end"})
    cases = [{}, {"human_review": "end"}, {"review_depth": m.OLD_DEFAULT_REVIEW_DEPTH},
             {"review_depth": {"low": 1}, "role_models": {"ratchet-implementer": "y"}, "host_models": {"anthropic": {"standard": "z"}}},
             {"providers": [{"id": "a"}], "standards": ["s"], "ui_reviewers": {"visual": ["q"]}}]
    for i, c in enumerate(cases):
        project_path(d).write_text(json.dumps(c))
        with th.InProcessCall(m, d, env, RESET_GLOBALS, LOCK_WAIT_CAP):
            got = m.Ctx(d).config()
        check(f"[CP1-X08] without a global file the layered config equals config_from_user (case {i})",
              got == m.config_from_user(c), got)


def x09_review_depth_is_project_only():
    d, env = project({"human_review": "end"}, glob={"review_depth": {"low": ["any-both"]}})
    r = run(d, env, "status")
    check("[CP1-X09] review_depth in the global file is refused, naming the file and the key",
          r.returncode != 0 and str(global_path(env)) in text(r) and "review_depth" in text(r), text(r)[:300])
    check("[CP1-X09] and the message says it belongs in the project's config.json",
          "config.json" in text(r) and "project" in text(r))


def x10_ui_reviewers_allowed_in_global():
    d, env = project({"human_review": "end"}, glob={"ui_reviewers": {"visual": ["codex"]}})
    cfg, r = effective(d, env)
    check("[CP1-X10] ui_reviewers in the global file is accepted and applies",
          cfg is not None and cfg["ui_reviewers"]["visual"] == ["codex"], text(r)[:300])


def x11_any_other_key_refused_in_global():
    for key, val in (("reviewers", 3), ("standards", ["a.md"]), ("design_md", "G.md"), ("test_timeout_sec", 9),
                     ("fingerprint_include", ["x"]), ("not_a_real_key", 1)):
        d, env = project({"human_review": "end"}, glob={key: val})
        r = run(d, env, "status")
        check(f"[CP1-X11] {key} in the global file stops the command naming the file and the key",
              r.returncode != 0 and str(global_path(env)) in text(r) and key in text(r), text(r)[:300])


def x12_global_sub_entry_does_not_merge_deeper():
    d, env = project({"human_review": "end"}, glob={"host_models": {"anthropic": {"standard": "g"}}})
    cfg, _ = effective(d, env)
    check("[CP1-X12] a partial global host_models.anthropic replaces the shipped entry whole, like the project's",
          cfg is not None and cfg["host_models"]["anthropic"] == {"standard": "g"}, cfg and cfg["host_models"])


LAYERED_KEYS = ("role_models", "host_models", "providers", "agents", "host_provider", "ui_reviewers")


def x13_no_direct_project_config_read_of_layered_keys():
    """Structural: a value read straight from the project's config file (not through Ctx.config / user_config) is
    never used for a key the machine-wide file may set, or the global layer would be bypassed."""
    files = sorted((ROOT / "skills").glob("*/scripts/*.py"))
    check("[CP1-X13] the scan sees the engine sources", any(f.name == "ratchet.py" for f in files))
    read_re = re.compile(r"(\w+)\s*=\s*\(?\s*(?:[\w.]+\(\s*)?(?:read_json|json\.loads)\([^\n]*(?:config_path|config\.json)")
    for f in files:
        src = f.read_text()
        for m in read_re.finditer(src):
            var = m.group(1)
            for key in LAYERED_KEYS:
                bad = re.search(r"\b" + var + r"\.get\(\s*[\"']" + key + r"[\"']|\b" + var + r"\[\s*[\"']" + key
                                + r"[\"']\](?!\s*=[^=])", src)  # a write (user[k] = ...) is fine, a read is not
                check(f"[CP1-X13] {f.name}: '{var}' read from the project file is not used for '{key}'", not bad)
        direct = re.search(r"config\.json[\"']\)?\)?\.read_text\(\)\)?\.get\(\s*[\"'](" + "|".join(LAYERED_KEYS) + ")",
                           src)
        check(f"[CP1-X13] {f.name}: no one-line project config read of a layered key", not direct)
    ui = (ROOT / "skills/ratchet-ui-compare/scripts/ui_review.py").read_text()
    check("[CP1-X13] ui_review.py gets ui_reviewers through the engine's merged config",
          "Ctx(eng.find_root()).config()" in ui)


def _function_nodes(path, names):
    import ast
    tree = ast.parse(Path(path).read_text())
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names}


def x14_merge_and_integrity_read_the_layered_config():
    """Structural (AST): the lane-merge check, the approval snapshot and the integrity check judge the project file
    laid over the global layer. In each, config_from_user only ever takes ctx.user_config(...), nothing reads the
    project's file by config_path, and the layered reader is really called. This catches what X13's one-line
    regexes cannot see, e.g. config_from_user(main_cfg) (project file alone) in cmd_merged."""
    import ast
    sc = ROOT / "skills/ratchet-orchestrator/scripts"
    want = {sc / "ratchet.py": ("cmd_merged",), sc / "run_records.py": ("integrity_problems", "approval_snapshot")}

    def fname(c):
        return c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
    for path, names in want.items():
        found = _function_nodes(path, names)
        for name in names:
            check(f"[CP1-X14] {name} is found in {path.name}", name in found)
            if name not in found:
                continue
            calls = [c for c in ast.walk(found[name]) if isinstance(c, ast.Call)]
            layered = [c for c in calls if fname(c) in ("config", "user_config")]
            check(f"[CP1-X14] {name} calls the layered reader (ctx.config / ctx.user_config)", bool(layered))
            bad_cfu = [c for c in calls if fname(c) == "config_from_user"
                       and not (c.args and isinstance(c.args[0], ast.Call) and fname(c.args[0]) == "user_config")]
            check(f"[CP1-X14] {name}: config_from_user only takes ctx.user_config(...)", not bad_cfu,
                  [ast.unparse(c) for c in bad_cfu])
            direct = [c for c in calls if fname(c) in ("read_json", "loads", "_read_record")
                      and "config_path" in ast.unparse(c)]
            check(f"[CP1-X14] {name}: the project's config file is not read on its own", not direct,
                  [ast.unparse(c) for c in direct])
    ip = _function_nodes(sc / "run_records.py", ("integrity_problems",)).get("integrity_problems")
    check("[CP1-X14] integrity_problems judges the approval snapshot",
          ip is not None and "approval_snapshot(" in ast.unparse(ip))


def _merge_project(glob):
    """tests/test_merge_main.py's lane (CP1 done, CP2 next), approved with `glob` as the global file."""
    import test_merge_main as mm
    gp = global_path(mm.tg.ENV)
    gp.parent.mkdir(parents=True, exist_ok=True)
    if glob is None:
        gp.unlink(missing_ok=True)
    else:
        gp.write_text(json.dumps(glob))
    return mm, mm.build("cfglayers"), gp


def _commit_main_config(mm, d, change):
    mm.git(d, "checkout", "-q", "-b", "main")
    p = d / ".ratchet/config.json"
    c = json.loads(p.read_text())
    c.update(change)
    p.write_text(json.dumps(c, indent=2) + "\n")
    (d / "src/m.js").write_text("export const tax = () => 0.2;\n")
    mm.commit_all(d, "M1 from main")
    mm.git(d, "checkout", "-q", "lane")
    mm.merge_main(d)


def x15_merged_lays_main_project_config_over_global():
    g = {"role_models": {"ratchet-implementer": "opus-x"}}
    mm, d, gp = _merge_project(g)
    try:
        # main changes an unrelated rule; the global role_models is unchanged since approval
        _commit_main_config(mm, d, {"max_behavior_attempts": 9})
        r = mm.rx(d, "merged")
        out = mm.said(r)
        check("[CP1-X15] main changing an unrelated key: merged is accepted", r.returncode == 0, out[-300:])
        check("[CP1-X15] no spurious approval is asked for the global role_models",
              "role_models" not in out and mm.REFUSAL not in out, out[-300:])
    finally:
        gp.unlink(missing_ok=True)
    mm, d, gp = _merge_project(g)
    try:
        # main sets the same locked key in the project file: merged takes main's exact version as the baseline
        _commit_main_config(mm, d, {"role_models": {"ratchet-fixer": "main-pick"}})
        r = mm.rx(d, "merged")
        out = mm.said(r)
        check("[CP1-X15] main changing role_models: merged accepts it as main's exact version "
              "(judged over the global layer, not the project file alone)",
              r.returncode == 0 and "Accepted from main as the new baseline: config rule 'role_models'" in out
              and "Still changed after approval" not in out, out[-400:])
        check("[CP1-X15] the global entry still applies under main's project entry",
              json.loads(mm.rx(d, "config").stdout)["role_models"]["ratchet-implementer"] == "opus-x")
    finally:
        gp.unlink(missing_ok=True)
    mm, d, gp = _merge_project(g)
    try:
        # the global file changes after approval, main touches only something else: still unapproved
        gp.write_text(json.dumps({"role_models": {"ratchet-implementer": "changed-later"}}))
        _commit_main_config(mm, d, {"max_behavior_attempts": 9})
        r = mm.rx(d, "merged")
        out = mm.said(r)
        check("[CP1-X15] a global change after approval is not waved through by an unrelated main change",
              "Still changed after approval (not exactly main's version): config rule 'role_models'" in out, out[-400:])
    finally:
        gp.unlink(missing_ok=True)


def x16_integrity_flags_global_locked_keys_only_when_changed():
    """CP1-T12 covers role_models. Here: host_models, host_provider and ui_reviewers each, changed vs unchanged."""
    for key, v1, v2 in (("host_models", {"anthropic": {"standard": "a"}}, {"anthropic": {"standard": "b"}}),
                        ("host_provider", "codex", "anthropic"),
                        ("ui_reviewers", {"visual": ["codex"]}, {"visual": ["gemini"]})):
        d, env = approved_project(glob_before={key: v1})
        put_global(env, {key: v2})
        r = run(d, env, "tests-written", "CP1")
        out = text(r)
        check(f"[CP1-X16] {key}: a changed global value is flagged by name",
              r.returncode != 0 and INTEGRITY_MARK in out and key in out, out[:300])
        d2, env2 = approved_project(glob_before={key: v1})
        r2 = run(d2, env2, "tests-written", "CP1")
        check(f"[CP1-X16] {key}: an unchanged global value is not flagged",
              INTEGRITY_MARK not in text(r2), text(r2)[:300])


def x17_global_nested_shapes_are_validated():
    cases = (("providers entry empty", {"providers": [{}]}, "providers[0]", "id"),
             ("providers entry no preset or command", {"providers": [{"id": "x"}]}, "providers[0]", "preset"),
             ("providers command not a list", {"providers": [{"id": "x", "command": "ls"}]}, "providers[0].command", "list"),
             ("providers preset unknown", {"providers": [{"id": "x", "preset": "nope"}]}, "providers[0].preset", "nope"),
             ("role_models value a number", {"role_models": {"ratchet-fixer": 3}}, "role_models.ratchet-fixer", "string"),
             ("host_models value a string", {"host_models": {"anthropic": "opus"}}, "host_models.anthropic", "object"),
             ("host_models tier a list", {"host_models": {"anthropic": {"strong": ["a"]}}}, "host_models.anthropic.strong", "string"),
             ("host_models alternates a string", {"host_models": {"anthropic": {"alternates": "a"}}},
              "host_models.anthropic.alternates", "list"),
             ("ui_reviewers value a string", {"ui_reviewers": {"visual": "gemini"}}, "ui_reviewers.visual", "list"),
             ("agents value a number", {"agents": {"x": 1}}, "agents.x", "string"),
             ("agents value an object", {"agents": {"x": {"model": "m"}}}, "agents.x", "string"),
             ("agents value empty", {"agents": {"x": ""}}, "agents.x", "non-empty"),
             ("agents value /model", {"agents": {"x": "/model"}}, "agents.x", "both parts"),
             ("agents value provider/", {"agents": {"x": "provider/"}}, "agents.x", "both parts"),
             ("agents empty role name", {"agents": {"": "m"}}, "agents key ''", "non-empty"),
             ("agents not an object", {"agents": ["m"]}, "agents", "object"),
             ("role_models value empty", {"role_models": {"ratchet-fixer": ""}}, "role_models.ratchet-fixer", "non-empty"),
             ("role_models empty key", {"role_models": {"": "strong"}}, "role_models key ''", "non-empty"),
             ("role_models not an object", {"role_models": ["x"]}, "role_models", "object"),
             ("host_models empty company", {"host_models": {"": {"strong": "m"}}}, "host_models key ''", "non-empty"),
             ("host_models tier empty", {"host_models": {"anthropic": {"strong": ""}}}, "host_models.anthropic.strong", "non-empty"),
             ("host_models empty tier name", {"host_models": {"anthropic": {"": "m"}}}, "host_models.anthropic key ''", "non-empty"),
             ("host_models alternates empty item", {"host_models": {"anthropic": {"alternates": ["a", ""]}}},
              "host_models.anthropic.alternates", "non-empty"),
             ("host_models alternates non-string item", {"host_models": {"anthropic": {"alternates": [1]}}},
              "host_models.anthropic.alternates", "list"),
             ("ui_reviewers empty name", {"ui_reviewers": {"visual": ["gemini", ""]}}, "ui_reviewers.visual", "non-empty"),
             ("ui_reviewers non-string item", {"ui_reviewers": {"visual": [1]}}, "ui_reviewers.visual", "list"),
             ("ui_reviewers empty lens", {"ui_reviewers": {"": ["gemini"]}}, "ui_reviewers key ''", "non-empty"),
             ("ui_reviewers not an object", {"ui_reviewers": ["x"]}, "ui_reviewers", "object"),
             ("host_provider empty", {"host_provider": ""}, "host_provider", "non-empty"),
             ("providers not a list", {"providers": {"id": "x"}}, "providers", "list"),
             ("providers entry not an object", {"providers": ["x"]}, "providers[0]", "object"),
             ("providers id empty", {"providers": [{"id": "", "preset": "codex"}]}, "providers[0]", "id"),
             ("providers duplicate id", {"providers": [{"id": "x", "preset": "codex"}, {"id": "x", "preset": "codex"}]},
              "providers[1].id", "twice"),
             ("providers command empty list", {"providers": [{"id": "x", "command": []}]}, "providers[0].command", "list"),
             ("providers command empty item", {"providers": [{"id": "x", "command": ["ls", ""]}]},
              "providers[0].command", "non-empty"),
             ("providers model empty", {"providers": [{"id": "x", "preset": "codex", "model": ""}]},
              "providers[0].model", "non-empty"),
             ("providers model a number", {"providers": [{"id": "x", "preset": "codex", "model": 1}]},
              "providers[0].model", "string"),
             ("providers company empty", {"providers": [{"id": "x", "preset": "codex", "company": ""}]},
              "providers[0].company", "non-empty"),
             ("providers cost empty", {"providers": [{"id": "x", "preset": "codex", "cost": ""}]},
              "providers[0].cost", "non-empty"),
             ("providers variant a number", {"providers": [{"id": "x", "preset": "codex", "variant": 1}]},
              "providers[0].variant", "string"),
             ("host_provider a list", {"host_provider": ["x"]}, "host_provider", "string"),
             ("providers stdin a string", {"providers": [{"id": "x", "preset": "codex", "stdin": "yes"}]},
              "providers[0].stdin", "true or false"),
             ("providers for_risk a string", {"providers": [{"id": "x", "preset": "codex", "for_risk": "high"}]},
              "providers[0].for_risk", "list"),
             ("providers for_risk unknown risk", {"providers": [{"id": "x", "preset": "codex", "for_risk": ["urgent"]}]},
              "providers[0].for_risk", "low, normal, high"),
             ("providers prefer_for a string", {"providers": [{"id": "x", "preset": "codex", "prefer_for": "both"}]},
              "providers[0].prefer_for", "list"),
             ("providers prefer_for unknown lens", {"providers": [{"id": "x", "preset": "codex", "prefer_for": ["nope"]}]},
              "providers[0].prefer_for", "correctness"))
    for name, bad, key, word in cases:
        d, env = project({"human_review": "end"}, glob=bad)
        for cmd in (("status",), ("models",)):
            r = run(d, env, *cmd)
            out = text(r)
            check(f"[CP1-X17] {name} ({cmd[0]}): stops naming the file and the key",
                  r.returncode != 0 and str(global_path(env)) in out and key in out and word in out
                  and "Traceback" not in out, out[:300])


def x18_valid_global_shapes_still_load():
    good = (("agents bare model", {"agents": {"fixer": "opus"}}),
            ("agents provider/model", {"agents": {"fixer": "codex/gpt-x"}}),
            ("agents provider/org/model", {"agents": {"reviewer": "opencode-glm/opencode-go/glm-5.3"},
                                                 "providers": [{"id": "opencode-glm", "preset": "opencode"}]}),
            ("agents a/b/c", {"agents": {"implementer": "codex/b/c"}}),
            ("agents model with a slash", {"agents": {"reviewer": "opencode/glm-5.3"}}),
            ("role_models tier and model", {"role_models": {"ratchet-fixer": "strong", "ratchet-implementer": "m-1"}}),
            ("host_models full", {"host_models": {"anthropic": {"strong": "a", "alternates": ["b", "c"]}}}),
            ("host_models empty alternates", {"host_models": {"anthropic": {"alternates": []}}}),
            ("ui_reviewers lists", {"ui_reviewers": {"visual": ["gemini"], "other": []}}),
            ("host_provider auto", {"host_provider": "auto"}),
            ("providers with extra field", {"providers": [{"id": "x", "preset": "codex", "model": "m",
                                                          "company": "openai", "cost": "flat", "variant": "v",
                                                          "prefer_for": ["both"], "for_risk": ["high"],
                                                          "stdin": True},
                                                         {"id": "y", "command": ["ls"]}]}))
    for name, ok_cfg in good:
        d, env = project({"human_review": "end"}, glob=ok_cfg)
        for cmd in (("status",), ("models",)):
            r = run(d, env, *cmd)
            out = text(r)
            check(f"[CP1-X18] {name} ({cmd[0]}): still loads", r.returncode == 0 and "Traceback" not in out,
                  out[:300])


def x19_every_provider_field_the_engine_reads_is_validated_globally():
    """Scan every engine script for the provider-entry fields it reads or writes (a constant key on `pv`/`prov`) and
    require each to be refused in the global file when wrong-shaped, or to be on EXEMPT with the reason it is not."""
    import ast
    exempt = {"launch_model": "never read from a settings file: it exists only in memory, on a provider copy made by "
                              "with_reviewer_choice / launch_cfg from agents.reviewer, so there is no file value to refuse"}
    # field -> "not validated because ..."
    wrong = {"id": 5, "preset": 5, "command": "ls", "model": 1, "company": 1, "cost": 1, "variant": 1, "stdin": "yes",
             "for_risk": "high", "prefer_for": "both", "paid_ok_at": "2026-01-01"}
    found = set()
    for path in sorted((ROOT / "skills").glob("*/scripts/*.py")):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in ("pv", "prov") \
                    and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                found.add(n.slice.value)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("get", "pop", "setdefault") \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id in ("pv", "prov") and n.args \
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                found.add(n.args[0].value)
    check("[CP1-X19] the scan finds the provider fields the engine is known to read",
          {"id", "preset", "command", "model", "stdin", "for_risk", "prefer_for"} <= found, sorted(found))
    for field in sorted(found):
        if field in exempt:
            check(f"[CP1-X19] {field}: exempt with a reason", bool(exempt[field].strip()))
            continue
        check(f"[CP1-X19] {field}: has a wrong-shaped value to test with (add it, or exempt it with a reason)",
              field in wrong, field)
        if field not in wrong:
            continue
        entry = {"id": "x", "preset": "codex"}
        entry[field] = wrong[field]
        d, env = project({"human_review": "end"}, glob={"providers": [entry]})
        r = run(d, env, "status")
        out = text(r)
        key = {"id": "providers[0]", "paid_ok_at": "providers[]"}.get(field, f"providers[0].{field}")
        check(f"[CP1-X19] {field}: a wrong-shaped global value stops naming the file and the key",
              r.returncode != 0 and str(global_path(env)) in out and key in out and field in out
              and "Traceback" not in out, out[:300])


TESTS = [
    ("CP1-X19", x19_every_provider_field_the_engine_reads_is_validated_globally),
    ("CP1-X18", x18_valid_global_shapes_still_load),
    ("CP1-X17", x17_global_nested_shapes_are_validated),
    ("CP1-X14", x14_merge_and_integrity_read_the_layered_config),
    ("CP1-X15", x15_merged_lays_main_project_config_over_global),
    ("CP1-X16", x16_integrity_flags_global_locked_keys_only_when_changed),
    ("CP1-X09", x09_review_depth_is_project_only), ("CP1-X10", x10_ui_reviewers_allowed_in_global),
    ("CP1-X11", x11_any_other_key_refused_in_global), ("CP1-X12", x12_global_sub_entry_does_not_merge_deeper),
    ("CP1-X13", x13_no_direct_project_config_read_of_layered_keys),
    ("CP1-X04", x04_init_does_not_hide_global_values), ("CP1-X05", x05_global_shapes_are_validated),
    ("CP1-X06", x06_damaged_global_stops_before_any_write), 
    ("CP1-X08", x08_user_config_matches_config_from_user_without_global),
    ("CP1-X01", x01_provider_writers_never_copy_global_list),
    ("CP1-X03", x03_project_sub_entry_replaces_whole),
    ("CP1-T01",t01_no_global_file_unchanged), ("CP1-T02", t02_global_model_applies),
    ("CP1-T03", t03_project_beats_global), ("CP1-T04", t04_non_dict_replaces_whole),
    ("CP1-T05", t05_old_default_review_depth_dropped_per_level), ("CP1-T06", t06_damaged_global_stops),
    ("CP1-T07", t07_global_not_an_object), ("CP1-T08", t08_paid_consent_refused_in_global),
    ("CP1-T09", t09_project_only_keys_refused_in_global), ("CP1-T10", t10_machine_wide_keys_load),
    ("CP1-T11", t11_writers_never_copy_global_values), ("CP1-T12", t12_locked_setting_in_global_asks_approval),
    ("CP1-T13", t13_unlocked_global_keys_do_not_disturb_run), ("CP1-T14", t14_only_the_global_file_in_use_counts),
    ("CP1-T15", t15_readme_lists_the_test_file), ("CP1-T16", t16_inventory_lists_the_file_when_it_scans_engine_source),
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
