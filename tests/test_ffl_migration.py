#!/usr/bin/env python3
"""
CP6 tests of the fast-follow cleanups run: old copied settings stop hiding machine-wide ones.   python3 tests/test_ffl_migration.py

Older Ratchet versions copied the shipped host_provider ('auto') and ui_reviewers (the shipped lens lists) into every
project's .ratchet/config.json. Such a copy looks like a value the developer chose, so it hides a machine-wide value for the
same key. The rule under test is read-time only (nothing on disk is rewritten): the project's host_provider or ui_reviewers is
skipped when it equals the shipped default exactly AND the machine-wide file sets that key; each key is judged on its own.
  * CP6-T01, T02, T03, T06, T09, T12, T13, T15 fail on the old code (the copy wins and --sources says project / builtin);
  * CP6-T04, T05, T07, T10, T11, T14 are no-over-flagging guards (a real project value wins, no machine-wide value changes
    nothing, the explicit unset still works, init's copy list is unchanged, a damaged machine-wide key is still refused);
  * CP6-T08 fails on the old code only through its "the machine-wide values are in effect" precondition; its real job is that
    the project file is never rewritten and `config --scope project` still prints the raw copy;
  * CP6-T16 reads the three documents and the README test list.

Design assumptions (the production wording is not pinned beyond what the cases state):
  * "old copy" means: host_provider == the shipped 'auto', or ui_reviewers == the shipped dict exactly (both lenses);
  * T05 pins only ui_reviewers.visual and the source of ui_reviewers: whether the project's second lens (an unchanged copy
    next to a real one) still beats the machine-wide lens is left open;
  * T16 looks, in each of README.md, skills/ratchet-orchestrator/SKILL.md and skills/ratchet-orchestrator/references/schemas.md,
    for one stretch of text (700 characters either side of a `host_provider` mention) that holds all of: ui_reviewers, the
    word old or older, copy/copied/copies, machine-wide, yield (any form), a form of rewrite, deliberately and default.
This file only drives the engine's CLI in-process (and reads the three documents), so it needs no test-inventory entry while it
never reads engine source.

Everything runs in-process through the engine's own entry points (standard 30), with the helpers of tests/test_config_layers.py:
a throwaway git project and a private per-user Ratchet home. No network, no real agent CLI, nothing is spent.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
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
import _tmp                                             # noqa: E402,F401
import trust_helpers as th                              # noqa: E402,F401
import test_config_layers as cl                         # noqa: E402  (in-process helpers: project(), run(), ...)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP6-[TX]\d+)\]")

README = ROOT / "README.md"
SKILL = ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md"
SCHEMAS = ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"


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
SHIPPED_UI = {"visual": ["gemini", "claude", "codex"], "behavior": ["claude", "gemini", "codex"]}
CODEX_UI = {"visual": ["codex"], "behavior": ["codex"]}


def shipped_ui():
    """The shipped ui_reviewers, read from the engine so a change to it shows up (and checked against the literal)."""
    return json.loads(json.dumps(cl.engine().DEFAULT_CONFIG["ui_reviewers"]))


def jout(r):
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def cfg_of(d, env):
    r = run(d, env, "config")
    return jout(r), r


def sources_of(d, env):
    r = run(d, env, "config", "--sources")
    return jout(r), r


def raw_project(d):
    return json.loads(cl.project_path(d).read_text())


def old_project(proj=None, glob=None, raw_glob=None):
    """A throwaway project whose file holds the copied host_provider and ui_reviewers (plus `proj` over them)."""
    cfg = {"host_provider": "auto", "ui_reviewers": shipped_ui()}
    cfg.update(proj or {})
    return cl.project(cfg, glob=glob, raw_glob=raw_glob)


# ------------------------------------------------------------------------------------------------ cases

def t01_old_copied_host_provider_yields_to_the_machine_wide_one():
    check("[CP6-T01] the fixture copy is the real shipped default", shipped_ui() == SHIPPED_UI
          and cl.engine().DEFAULT_CONFIG["host_provider"] == "auto")
    d, env = old_project(glob={"host_provider": "codex"})
    cfg, r = cfg_of(d, env)
    check("[CP6-T01] config succeeds", r.returncode == 0 and cfg is not None, text(r)[:200])
    check("[CP6-T01] the effective host_provider is the machine-wide 'codex', not the copied 'auto'",
          cfg is not None and cfg.get("host_provider") == "codex", cfg and cfg.get("host_provider"))
    src, r2 = sources_of(d, env)
    check("[CP6-T01] --sources reports host_provider as global",
          src is not None and src.get("host_provider") == "global", src and src.get("host_provider"))
    check("[CP6-T01] the unrelated copied ui_reviewers (global is silent on it) still comes from the project",
          src is not None and src.get("ui_reviewers") == "project", src and src.get("ui_reviewers"))


def t02_old_copied_lens_lists_yield_to_the_machine_wide_ones():
    d, env = old_project(glob={"ui_reviewers": CODEX_UI})
    cfg, r = cfg_of(d, env)
    ui = (cfg or {}).get("ui_reviewers") or {}
    check("[CP6-T02] the effective visual lens list is ['codex']", ui.get("visual") == ["codex"], ui)
    check("[CP6-T02] the effective behavior lens list is ['codex']", ui.get("behavior") == ["codex"], ui)
    src, _ = sources_of(d, env)
    check("[CP6-T02] --sources says ui_reviewers comes from global",
          src is not None and src.get("ui_reviewers") == "global", src and src.get("ui_reviewers"))
    check("[CP6-T02] host_provider (global is silent on it) is still the project's copy",
          cfg is not None and cfg.get("host_provider") == "auto" and src is not None
          and src.get("host_provider") == "project", cfg and cfg.get("host_provider"))


def t03_models_view_shows_the_machine_wide_lens_order_and_global_source():
    d, env = old_project(glob={"ui_reviewers": {"visual": ["codex"]}})
    r = run(d, env, "models", "--json")
    data = jout(r) or {}
    row = {e.get("role"): e for e in data.get("detail", []) if isinstance(e, dict)}.get("ui-reviewer-visual") or {}
    check("[CP6-T03] models --json has a ui-reviewer-visual row", bool(row), text(r)[:200])
    check("[CP6-T03] the requested order is the machine-wide 'codex'", row.get("requested") == "codex", row)
    check("[CP6-T03] its source is global, not project or builtin", row.get("source") == "global", row)
    behavior = {e.get("role"): e for e in data.get("detail", []) if isinstance(e, dict)}.get("ui-reviewer-behavior") or {}
    check("[CP6-T03] the behavior lens (global is silent on it) keeps the shipped order",
          behavior.get("requested") == ", ".join(SHIPPED_UI["behavior"]), behavior)


def t04_a_host_provider_really_set_in_the_project_still_wins():
    d, env = old_project({"host_provider": "google"}, glob={"host_provider": "codex"})
    cfg, r = cfg_of(d, env)
    check("[CP6-T04] the effective host_provider is the project's 'google'",
          cfg is not None and cfg.get("host_provider") == "google", cfg and cfg.get("host_provider"))
    src, _ = sources_of(d, env)
    check("[CP6-T04] --sources says project", src is not None and src.get("host_provider") == "project",
          src and src.get("host_provider"))


def t05_a_lens_list_really_changed_in_the_project_is_kept():
    proj_ui = {"visual": ["claude"], "behavior": list(SHIPPED_UI["behavior"])}
    d, env = old_project({"ui_reviewers": proj_ui}, glob={"ui_reviewers": CODEX_UI})
    cfg, r = cfg_of(d, env)
    ui = (cfg or {}).get("ui_reviewers") or {}
    check("[CP6-T05] the project's visual list ['claude'] wins", ui.get("visual") == ["claude"], ui)
    src, _ = sources_of(d, env)
    check("[CP6-T05] --sources says ui_reviewers is project",
          src is not None and src.get("ui_reviewers") == "project", src and src.get("ui_reviewers"))
    check("[CP6-T05] the project file is untouched", raw_project(d)["ui_reviewers"] == proj_ui, raw_project(d))
    # a tiny difference (one provider dropped from one lens) still counts as a real choice: only an exact copy is dropped
    near = {"visual": ["gemini", "claude"], "behavior": list(SHIPPED_UI["behavior"])}
    d2, env2 = old_project({"ui_reviewers": near}, glob={"ui_reviewers": CODEX_UI})
    cfg2, _ = cfg_of(d2, env2)
    src2, _ = sources_of(d2, env2)
    check("[CP6-T05] a list one entry short of the default is not a copy: the project's visual list wins",
          ((cfg2 or {}).get("ui_reviewers") or {}).get("visual") == ["gemini", "claude"]
          and src2 is not None and src2.get("ui_reviewers") == "project", cfg2 and cfg2.get("ui_reviewers"))


def t06_a_mixed_project_keeps_the_real_value_and_drops_only_the_copy():
    d, env = old_project({"ui_reviewers": {"visual": ["claude"]}},
                         glob={"host_provider": "codex", "ui_reviewers": CODEX_UI})
    src, _ = sources_of(d, env)
    cfg, r = cfg_of(d, env)
    check("[CP6-T06] host_provider (a copy) is reported as global",
          src is not None and src.get("host_provider") == "global", src and src.get("host_provider"))
    check("[CP6-T06] ui_reviewers (a real choice) is reported as project",
          src is not None and src.get("ui_reviewers") == "project", src and src.get("ui_reviewers"))
    ui = (cfg or {}).get("ui_reviewers") or {}
    check("[CP6-T06] the effective host_provider is the machine-wide 'codex'",
          cfg is not None and cfg.get("host_provider") == "codex", cfg and cfg.get("host_provider"))
    check("[CP6-T06] the effective visual list is the project's ['claude']", ui.get("visual") == ["claude"], ui)
    check("[CP6-T06] the lens the project does not set keeps the machine-wide list (sub-entries merge as before)",
          ui.get("behavior") == ["codex"], ui)


def t07_with_no_machine_wide_value_an_old_copy_changes_nothing():
    for label, glob in (("no machine-wide file", None),
                        ("a machine-wide file that sets only role_models", {"role_models": {"ratchet-implementer": "opus-x"}})):
        d, env = old_project(glob=glob)
        cfg, r = cfg_of(d, env)
        check(f"[CP6-T07] {label}: host_provider stays the copied 'auto'",
              cfg is not None and cfg.get("host_provider") == "auto", cfg and cfg.get("host_provider"))
        check(f"[CP6-T07] {label}: the lens lists stay the shipped ones",
              cfg is not None and cfg.get("ui_reviewers") == SHIPPED_UI, cfg and cfg.get("ui_reviewers"))
        src, _ = sources_of(d, env)
        check(f"[CP6-T07] {label}: --sources still says project for both keys",
              src is not None and src.get("host_provider") == "project" and src.get("ui_reviewers") == "project",
              src and (src.get("host_provider"), src.get("ui_reviewers")))
    d, env = old_project()
    cfg, _ = cfg_of(d, env)
    check("[CP6-T07] with no machine-wide file the whole effective config equals the old merge",
          cfg == cl.old_merge(raw_project(d)))


def t08_the_project_file_is_never_rewritten_and_the_raw_file_shows_the_copy():
    glob = {"host_provider": "codex", "ui_reviewers": CODEX_UI}
    d, env = old_project(glob=glob)
    p = cl.project_path(d)
    before, mtime = p.read_bytes(), p.stat().st_mtime_ns
    cfg, _ = cfg_of(d, env)
    check("[CP6-T08] the machine-wide values are in effect (so the old copy really was skipped)",
          cfg is not None and cfg.get("host_provider") == "codex"
          and (cfg.get("ui_reviewers") or {}).get("visual") == ["codex"], cfg and cfg.get("host_provider"))
    results = [run(d, env, *a) for a in (("config", "--sources"), ("models",), ("models", "--json"), ("status",))]
    check("[CP6-T08] config --sources, models and status all succeed",
          all(r.returncode == 0 for r in results), [text(r)[:100] for r in results if r.returncode])
    check("[CP6-T08] the project file's bytes are identical after those commands", p.read_bytes() == before)
    check("[CP6-T08] and its modified time is unchanged", p.stat().st_mtime_ns == mtime)
    r = run(d, env, "config", "--scope", "project")
    shown = jout(r)
    check("[CP6-T08] config --scope project still prints the copied host_provider and ui_reviewers as on disk",
          shown is not None and shown.get("host_provider") == "auto" and shown.get("ui_reviewers") == SHIPPED_UI,
          text(r)[:200])
    check("[CP6-T08] the bytes are still identical after that too", p.read_bytes() == before)
    check("[CP6-T08] the machine-wide file is untouched", json.loads(cl.global_path(env).read_text()) == glob)


def t09_running_any_command_twice_gives_the_same_answer():
    d, env = old_project(glob={"host_provider": "codex", "ui_reviewers": CODEX_UI})
    first = [run(d, env, "config"), run(d, env, "config", "--sources")]
    second = [run(d, env, "config"), run(d, env, "config", "--sources")]
    check("[CP6-T09] config prints the same bytes both times", first[0].stdout == second[0].stdout and bool(first[0].stdout))
    check("[CP6-T09] config --sources prints the same bytes both times",
          first[1].stdout == second[1].stdout and bool(first[1].stdout))
    for label, rs in (("first", first), ("second", second)):
        cfg, src = jout(rs[0]) or {}, jout(rs[1]) or {}
        check(f"[CP6-T09] the {label} run shows the machine-wide values from global",
              cfg.get("host_provider") == "codex" and (cfg.get("ui_reviewers") or {}).get("visual") == ["codex"]
              and src.get("host_provider") == "global" and src.get("ui_reviewers") == "global",
              (cfg.get("host_provider"), src.get("host_provider"), src.get("ui_reviewers")))


def t10_removing_the_machine_wide_value_brings_the_projects_back_and_unset_works():
    d, env = old_project(glob={"host_provider": "codex"})
    cfg, _ = cfg_of(d, env)
    check("[CP6-T10] to begin with the machine-wide 'codex' is effective",
          cfg is not None and cfg.get("host_provider") == "codex", cfg and cfg.get("host_provider"))
    r = run(d, env, "config", "--global", "--unset", "host_provider")
    check("[CP6-T10] the global unset succeeds and empties the machine-wide key",
          r.returncode == 0 and "host_provider" not in json.loads(cl.global_path(env).read_text()), text(r)[:200])
    cfg, _ = cfg_of(d, env)
    src, _ = sources_of(d, env)
    check("[CP6-T10] after the global unset the project's own 'auto' applies",
          cfg is not None and cfg.get("host_provider") == "auto", cfg and cfg.get("host_provider"))
    check("[CP6-T10] and --sources says project", src is not None and src.get("host_provider") == "project",
          src and src.get("host_provider"))
    r2 = run(d, env, "config", "--unset", "host_provider")
    check("[CP6-T10] the project unset succeeds", r2.returncode == 0, text(r2)[:200])
    check("[CP6-T10] the key is gone from the project file", "host_provider" not in raw_project(d), raw_project(d))
    src, _ = sources_of(d, env)
    check("[CP6-T10] and --sources now says builtin", src is not None and src.get("host_provider") == "builtin",
          src and src.get("host_provider"))
    # the explicit unset also works while the machine-wide value is still there
    d2, env2 = old_project(glob={"host_provider": "codex"})
    r3 = run(d2, env2, "config", "--unset", "host_provider")
    cfg2, _ = cfg_of(d2, env2)
    src2, _ = sources_of(d2, env2)
    check("[CP6-T10] unsetting the copy in the project removes it from the file and the machine-wide value stays in effect",
          r3.returncode == 0 and "host_provider" not in raw_project(d2) and cfg2 is not None
          and cfg2.get("host_provider") == "codex" and src2 is not None and src2.get("host_provider") == "global",
          text(r3)[:200])


def t11_a_fresh_init_copies_no_machine_wide_key_and_the_rest_is_unchanged():
    d, env = cl.fresh_project({"host_provider": "codex"})
    r = run(d, env, "init", "feature", "--slug", "f")
    check("[CP6-T11] init succeeds", r.returncode == 0, text(r)[:200])
    r2 = run(d, env, "config", "--scope", "project")
    on_disk = jout(r2) or {}
    check("[CP6-T11] the new project file has no host_provider", "host_provider" not in on_disk, on_disk)
    check("[CP6-T11] it still carries ui_reviewers, equal to the shipped value", on_disk.get("ui_reviewers") == SHIPPED_UI,
          on_disk.get("ui_reviewers"))
    defaults = cl.engine().DEFAULT_CONFIG
    keys = ["human_review", "ui_reviewers", "regression_command", "commit_per_checkpoint", "design_md", "standards", "app"]
    check("[CP6-T11] and the other defaults it always copied, so init's copy list is unchanged",
          on_disk == {k: defaults[k] for k in keys}, sorted(on_disk))
    cfg, _ = cfg_of(d, env)
    check("[CP6-T11] the effective host_provider is the machine-wide 'codex'",
          cfg is not None and cfg.get("host_provider") == "codex", cfg and cfg.get("host_provider"))
    d2, env2 = cl.fresh_project()
    run(d2, env2, "init", "feature", "--slug", "f")
    keys2 = keys + ["host_provider"]
    check("[CP6-T11] with no machine-wide file init still copies host_provider too",
          raw_project(d2) == {k: defaults[k] for k in keys2}, sorted(raw_project(d2)))


def t12_a_copy_made_before_the_machine_wide_file_existed_stops_hiding_it():
    d, env = cl.fresh_project()
    r = run(d, env, "init", "feature", "--slug", "f")
    check("[CP6-T12] init with no machine-wide file copies host_provider and ui_reviewers",
          r.returncode == 0 and raw_project(d).get("host_provider") == "auto"
          and raw_project(d).get("ui_reviewers") == SHIPPED_UI, text(r)[:200])
    cl.put_global(env, {"host_provider": "codex"})
    src, _ = sources_of(d, env)
    cfg, _ = cfg_of(d, env)
    check("[CP6-T12] after the machine-wide file is added, --sources says host_provider is global",
          src is not None and src.get("host_provider") == "global", src and src.get("host_provider"))
    check("[CP6-T12] and the effective host_provider is 'codex'",
          cfg is not None and cfg.get("host_provider") == "codex", cfg and cfg.get("host_provider"))
    cl.put_global(env, {"host_provider": "codex", "ui_reviewers": CODEX_UI})
    src, _ = sources_of(d, env)
    check("[CP6-T12] a machine-wide ui_reviewers added later is honoured too",
          src is not None and src.get("ui_reviewers") == "global", src and src.get("ui_reviewers"))


def t13_review_depth_old_default_rule_and_other_project_keys_are_unaffected():
    m = cl.engine()
    reviewers = {"host-standards": {"kind": "agent", "lens": "standards", "vendor": "host"}}
    d, env = cl.project({"host_provider": "auto", "review_depth": json.loads(json.dumps(m.OLD_DEFAULT_REVIEW_DEPTH)),
                         "reviewers": reviewers}, glob={"host_provider": "codex"})
    src, r = sources_of(d, env)
    check("[CP6-T13] --sources succeeds", src is not None, text(r)[:200])
    src = src or {}
    check("[CP6-T13] review_depth (old default) is still reported as builtin", src.get("review_depth") == "builtin",
          src.get("review_depth"))
    check("[CP6-T13] reviewers is still reported as project", src.get("reviewers") == "project", src.get("reviewers"))
    check("[CP6-T13] host_provider moves to global", src.get("host_provider") == "global", src.get("host_provider"))
    others = {k: v for k, v in src.items() if k not in ("review_depth", "reviewers", "host_provider")}
    check("[CP6-T13] nothing else moved: every other key is builtin",
          all(v == "builtin" for v in others.values()), {k: v for k, v in others.items() if v != "builtin"})


def t14_a_copy_next_to_a_damaged_machine_wide_key_is_still_refused():
    d, env = old_project(glob={"host_provider": ""})
    r = run(d, env, "config")
    out = text(r)
    check("[CP6-T14] config is refused", r.returncode != 0 and not r.stdout.strip(), out[:200])
    check("[CP6-T14] the message names the machine-wide file and host_provider",
          str(cl.global_path(env)) in out and "host_provider" in out, out[:300])
    check("[CP6-T14] it is one plain line with no traceback",
          "Traceback" not in out and len([ln for ln in out.splitlines() if ln.strip()]) == 1, out[:300])
    r2 = run(d, env, "config", "--sources")
    check("[CP6-T14] config --sources is refused the same way",
          r2.returncode != 0 and str(cl.global_path(env)) in text(r2) and "Traceback" not in text(r2), text(r2)[:200])


def t15_the_documented_limit_a_deliberately_chosen_default_is_treated_as_a_copy():
    d, env = cl.project({"host_provider": "auto"}, glob={"host_provider": "codex"})
    src, _ = sources_of(d, env)
    cfg, _ = cfg_of(d, env)
    check("[CP6-T15] the machine-wide 'codex' wins over a project value that equals the shipped default",
          cfg is not None and cfg.get("host_provider") == "codex", cfg and cfg.get("host_provider"))
    check("[CP6-T15] and the source is global", src is not None and src.get("host_provider") == "global",
          src and src.get("host_provider"))


def _windows(doc_text):
    t = doc_text.lower()
    for m in re.finditer("host_provider", t):
        yield t[max(0, m.start() - 700):m.end() + 700]


def t16_docs_and_the_test_list_describe_the_old_copy_rule():
    needs = (("ui_reviewers", r"ui_reviewers"), ("the word old/older", r"\bolder?\b"), ("copy/copied/copies", r"\bcop(?:y|ied|ies)\b"),
             ("machine-wide", r"machine-wide"), ("yield", r"\byield"), ("a form of rewrite", r"\brewr(?:ite|itten|ites|iting)\b|\brewrote\b"),
             ("deliberately", r"\bdeliberately\b"), ("default", r"\bdefaults?\b"))
    for label, path in (("README.md", README), ("skills/ratchet-orchestrator/SKILL.md", SKILL),
                        ("skills/ratchet-orchestrator/references/schemas.md", SCHEMAS)):
        windows = list(_windows(path.read_text()))
        best = max((sum(1 for _, pat in needs if re.search(pat, w)) for w in windows), default=0)
        missing = []
        if windows:
            w = max(windows, key=lambda w: sum(1 for _, pat in needs if re.search(pat, w)))
            missing = [n for n, pat in needs if not re.search(pat, w)]
        check(f"[CP6-T16] {label} has one passage that says an old project's copied host_provider and ui_reviewers (the shipped "
              "defaults) yield to a machine-wide value, that nothing is rewritten, and names the deliberate-default case",
              best == len(needs), f"best passage lacks: {missing}")
    section = README.read_text().split("### Running the tests", 1)[-1]
    check("[CP6-T16] the README 'Running the tests' list holds the line `python3 tests/test_ffl_migration.py`",
          "python3 tests/test_ffl_migration.py" in [ln.strip() for ln in section.splitlines()], None)
    me_src = Path(__file__).read_text()
    # The live pattern of tests/test_modularize_inventory.py, assembled from pieces so this check never matches itself.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(me_src) or src_pat.search(me_src))
    inv = INVENTORY.read_text()
    check("[CP6-T16] this file reads no engine source, or else it is named in the test inventory",
          (not scans) or "test_ffl_migration.py" in inv, f"scans={scans}")
    check("[CP6-T16] the earlier files' inventory entries are still there (the inventory stays consistent)",
          "test_ffl_wording.py" in inv, None)


TESTS = [
    ("CP6-T01", t01_old_copied_host_provider_yields_to_the_machine_wide_one),
    ("CP6-T02", t02_old_copied_lens_lists_yield_to_the_machine_wide_ones),
    ("CP6-T03", t03_models_view_shows_the_machine_wide_lens_order_and_global_source),
    ("CP6-T04", t04_a_host_provider_really_set_in_the_project_still_wins),
    ("CP6-T05", t05_a_lens_list_really_changed_in_the_project_is_kept),
    ("CP6-T06", t06_a_mixed_project_keeps_the_real_value_and_drops_only_the_copy),
    ("CP6-T07", t07_with_no_machine_wide_value_an_old_copy_changes_nothing),
    ("CP6-T08", t08_the_project_file_is_never_rewritten_and_the_raw_file_shows_the_copy),
    ("CP6-T09", t09_running_any_command_twice_gives_the_same_answer),
    ("CP6-T10", t10_removing_the_machine_wide_value_brings_the_projects_back_and_unset_works),
    ("CP6-T11", t11_a_fresh_init_copies_no_machine_wide_key_and_the_rest_is_unchanged),
    ("CP6-T12", t12_a_copy_made_before_the_machine_wide_file_existed_stops_hiding_it),
    ("CP6-T13", t13_review_depth_old_default_rule_and_other_project_keys_are_unaffected),
    ("CP6-T14", t14_a_copy_next_to_a_damaged_machine_wide_key_is_still_refused),
    ("CP6-T15", t15_the_documented_limit_a_deliberately_chosen_default_is_treated_as_a_copy),
    ("CP6-T16", t16_docs_and_the_test_list_describe_the_old_copy_rule),
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
