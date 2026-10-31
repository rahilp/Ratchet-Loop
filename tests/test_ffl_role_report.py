#!/usr/bin/env python3
"""
CP2 tests of the fast-follow cleanups run: role rows name the real default.   python3 tests/test_ffl_role_report.py

A developer (or the agent acting for them) reads the read-only `models` view (plain text and `--json`) to see which model
each sub-agent role really runs on. Two things must be true there:
  * a role nothing sets names the shipped default it runs on (its tier and the model of that tier), in the detail rows,
    the "asked ...; uses ..." lines and the spawn table (CP2-T06, T07, T13: guards that pin today's output so the fix
    cannot change what is shown for a normal role);
  * a misspelled role_models value is shown as invalid with a plain reason, never as the model in use, including when it
    sits below an agents choice that cannot apply on this host (CP2-T01 to T04 fail on the old code; CP2-T05, T08 to
    T12 are no-over-flagging guards: a direct typo stays invalid, a full model id is never called invalid, a valid value
    above a typo wins, and what really launches (`brief`, the launch guard) is unchanged).
CP2-T14 checks that this file is listed in the README's "Running the tests" list and, if it ever reads the engine's source
text, in tests/fixtures/modularize_cp1/test-inventory.md. This file only drives the engine's CLI, so no inventory entry
is required while it does not.

Design assumptions (the production wording is not pinned beyond what the cases state):
  * the invalid wording is the word "invalid" or "unknown" next to the misspelled name, with the valid names
    (haiku, inherit, light, opus, sonnet, standard, strong); a full model id such as claude-opus-4-1 only ever gets
    "unknown ... passed on as written", never "invalid";
  * the "asked ...; uses ...; from ..." line of a role is its row in the second table of plain `models`, and its `why:`
    lines are the indented lines under it; the spawn table is the first table, from the "Host:" line to the first blank
    line;
  * the old JSON `roles` map is deliberately not pinned for a typo (an open question); it is pinned only for the
    no-setting and the valid-setting worlds, where it equals the recorded pre-change values.

Everything runs in-process through the engine's own entry points (standard 30), with the helpers of
tests/test_config_layers.py, tests/test_agents_session.py, tests/test_agents_launch.py and tests/test_models_explain.py:
a throwaway git project, a private per-user Ratchet home, the host pinned per case. No network, no real agent CLI,
nothing is spent.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP2-T01",
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
import test_agents_session as se                        # noqa: E402  (fresh(): a project pinned to a host; rows())
import test_agents_launch as al                         # noqa: E402  (building(): a project with an approved plan)
import test_models_explain as me                        # noqa: E402  (OLD_ROLES, OLD_HEADER)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP2-[TX]\d+)\]")

README = ROOT / "README.md"
INVENTORY = ROOT / "tests" / "fixtures" / "modularize_cp1" / "test-inventory.md"

TW = "ratchet-test-writer"
IMPL = "ratchet-implementer"
OLD_ROLES, OLD_HEADER = me.OLD_ROLES, me.OLD_HEADER
SESSION = "your session's model"
VALID_NAMES = ("haiku", "inherit", "light", "opus", "sonnet", "standard", "strong")
INVALID = re.compile(r"\binvalid\b|\bunknown\b", re.I)
INVALID_ONLY = re.compile(r"\binvalid\b", re.I)
CANT_APPLY = re.compile(r"can(?:no|['’])t (?:use|apply)|cannot (?:use|apply)", re.I)
# The shipped tier of each of the 13 sub-agent roles (DEFAULT_CONFIG role_models), written out so a change shows up.
TIERS = {
    "ratchet-adversary": "strong", "ratchet-app-scout": "standard", "ratchet-behavior-reviewer": "standard",
    "ratchet-checkpoint-planner": "strong", "ratchet-design-system": "standard", "ratchet-fixer": "strong",
    "ratchet-implementer": "strong", "ratchet-prototyper": "standard", "ratchet-test-planner": "standard",
    "ratchet-test-runner": "light", "ratchet-test-writer": "standard", "ratchet-variant-writer": "alternate",
    "ratchet-visual-reviewer": "strong",
}


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
AGENT_OTHER = {"agents": {"test-writer": "openai/gpt-5"}}      # a choice that cannot apply on an Anthropic host


def world(proj=None, glob=None, host="claude"):
    """A throwaway git project (optional project config), a private machine-wide file (optional) and the host pinned."""
    return se.fresh(proj if proj is not None else {}, glob=glob, host=host)


def rm(role, value):
    return {"role_models": {role: value}}


def view(d, env):
    """(parsed `models --json`, {role: detail row}, the --json call, the plain call)"""
    rj = run(d, env, "models", "--json")
    rt = run(d, env, "models")
    data = se.jload(rj)
    return data, se.rows(data), rj, rt


def spawn_rows(out):
    """{role: model text} from the first table of plain `models` (from the Host: line to the first blank line)."""
    rows, on = {}, False
    for ln in out.splitlines():
        if ln.startswith("Host:"):
            on = True
            continue
        if on:
            if not ln.strip():
                break
            m = re.match(r"^  (ratchet-[\w-]+)\s+(.*?)\s*$", ln)
            if m:
                rows[m.group(1)] = m.group(2)
    return rows


def asked_block(out, role):
    """The role's 'asked ...; uses ...; from ...' line and the indented lines under it, as a list of lines."""
    block = []
    for ln in out.splitlines():
        if not block:
            if re.match(rf"^  {re.escape(role)}\s+asked ", ln):
                block = [ln]
        elif ln.startswith(" " * 20):
            block.append(ln)
        else:
            break
    return block


def old_spawn_lines():
    return [OLD_HEADER] + [f"  {role:<28} {m}" for role, m in sorted(OLD_ROLES.items())]


def others_unchanged(spawn, skip, expected=None):
    """The spawn rows of every role but `skip` equal the recorded defaults (or `expected`)."""
    want = expected or OLD_ROLES
    return all(spawn.get(r) == m for r, m in want.items() if r != skip)


def disk(d, env):
    return se.bytes_of(cl.project_path(d), cl.global_path(env))


def reason_of(row):
    return str(row.get("reason") or "")


def has_valid_names(s):
    return all(re.search(rf"\b{n}\b", s) for n in VALID_NAMES)


# ------------------------------------------------------------------------------------------------ T01 to T04: failing

def t01_typo_below_a_choice_that_cannot_apply_is_invalid_in_the_detail_row():
    d, env = world(AGENT_OTHER, rm(TW, "sonet"))
    data, rows, rj, rt = view(d, env)
    row = rows.get(TW, {})
    why = reason_of(row)
    check("[CP2-T01] `models --json` exits 0 with no traceback and the ratchet-test-writer row exists",
          rj.returncode == 0 and "Traceback" not in text(rj) and bool(row), (rj.returncode, text(rj)[:200]))
    check("[CP2-T01] the row's requested value is the agents choice openai/gpt-5",
          row.get("requested") == "openai/gpt-5", row)
    check("[CP2-T01] the row's effective model is sonnet (the shipped standard-tier model), never sonet",
          row.get("effective") == "sonnet", row)
    check("[CP2-T01] the reason names sonet as invalid or unknown",
          re.search(r"\bsonet\b", why) is not None and INVALID.search(why) is not None, why)
    check("[CP2-T01] the reason lists the valid names", has_valid_names(why), why)
    check("[CP2-T01] the reason still says the openai/gpt-5 choice cannot apply on this host",
          CANT_APPLY.search(why) is not None and re.search(r"openai/gpt-5|another company", why) is not None, why)


def t02_plain_text_and_note_never_present_the_typo_as_the_model_in_use():
    d, env = world(AGENT_OTHER, rm(TW, "sonet"))
    before = disk(d, env)
    r = run(d, env, "models")
    out, err = r.stdout, r.stderr
    spawn = spawn_rows(out)
    block = asked_block(out, TW)
    first = block[0] if block else ""
    check("[CP2-T02] plain `models` exits 0 and prints no traceback", r.returncode == 0 and "Traceback" not in text(r),
          (r.returncode, text(r)[:200]))
    check("[CP2-T02] no line says the role uses sonet",
          not [ln for ln in text(r).splitlines() if re.search(r"\buses sonet\b", ln)],
          [ln for ln in text(r).splitlines() if re.search(r"\buses sonet\b", ln)])
    check("[CP2-T02] the spawn-table row for ratchet-test-writer is present and is not sonet",
          TW in spawn and spawn.get(TW) != "sonet", spawn.get(TW))
    check("[CP2-T02] the 'asked ...; uses ...' line says it uses sonnet", re.search(r"; uses sonnet\b", first) is not None,
          first)
    note = [ln for ln in err.splitlines() if "agents.test-writer" in ln]
    check("[CP2-T02] the RATCHET note for agents.test-writer is still printed", bool(note), err[:300])
    check("[CP2-T02] that note does not say the role keeps (or runs on) the model sonet",
          not [ln for ln in note if re.search(r"\bkeeps\b[^;]*?\bmodel,?\s+['\"]?sonet\b|\b(?:uses|runs on) sonet\b", ln)],
          note)
    check("[CP2-T02] the text calls sonet invalid or unknown",
          any(re.search(r"\bsonet\b", ln) and INVALID.search(ln) for ln in text(r).splitlines()), text(r)[:300])
    check("[CP2-T02] neither settings file changed on disk", disk(d, env) == before)


def t03_a_role_whose_default_is_the_sessions_model_hides_the_typo_too():
    d, env = world({"agents": {"implementer": "openai/gpt-5"}}, rm(IMPL, "opsu"))
    data, rows, rj, rt = view(d, env)
    row = rows.get(IMPL, {})
    why = reason_of(row)
    check("[CP2-T03] `models --json` and plain `models` exit 0 with no traceback",
          rj.returncode == 0 and rt.returncode == 0 and "Traceback" not in text(rj) + text(rt),
          (rj.returncode, rt.returncode))
    check("[CP2-T03] the implementer row asks openai/gpt-5 and its effective model is inherit, never opsu",
          row.get("requested") == "openai/gpt-5" and row.get("effective") == "inherit", row)
    check("[CP2-T03] the reason names opsu as invalid or unknown",
          re.search(r"\bopsu\b", why) is not None and INVALID.search(why) is not None, why)
    block = asked_block(rt.stdout, IMPL)
    check("[CP2-T03] the plain 'asked ...; uses ...' line says it uses your session's model",
          bool(block) and f"; uses {SESSION}" in block[0], block[:1])
    check("[CP2-T03] no plain line says the implementer uses opsu and the spawn row is not opsu",
          not [ln for ln in rt.stdout.splitlines() if re.search(r"\buses opsu\b", ln)]
          and spawn_rows(rt.stdout).get(IMPL) not in (None, "opsu"), spawn_rows(rt.stdout).get(IMPL))


def t04_a_direct_typo_is_not_a_spawn_model_in_either_settings_file():
    for where in ("project", "global"):
        d, env = world(rm(TW, "sonet") if where == "project" else {}, rm(TW, "sonet") if where == "global" else None)
        before = disk(d, env)
        r = run(d, env, "models")
        spawn = spawn_rows(r.stdout)
        row = spawn.get(TW)
        block = asked_block(r.stdout, TW)
        first = block[0] if block else ""
        check(f"[CP2-T04] ({where}) plain `models` exits 0 and prints no traceback",
              r.returncode == 0 and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
        check(f"[CP2-T04] ({where}) the spawn row for ratchet-test-writer exists and neither equals nor ends in sonet",
              row is not None and row != "sonet" and not row.endswith("sonet"), row)
        check(f"[CP2-T04] ({where}) the asked/uses line still says asked sonet and uses sonnet, from the {where} file",
              re.search(rf"asked sonet; uses sonnet; from {where}\b", first) is not None, first)
        check(f"[CP2-T04] ({where}) that line's reason says sonet is invalid or unknown",
              INVALID.search("\n".join(block[1:])) is not None and "sonet" in "\n".join(block), block)
        check(f"[CP2-T04] ({where}) the other twelve spawn rows and the header are unchanged",
              spawn and others_unchanged(spawn, TW) and r.stdout.splitlines()[:1] == [OLD_HEADER], spawn)
        check(f"[CP2-T04] ({where}) neither settings file changed on disk", disk(d, env) == before)


# ------------------------------------------------------------------------------------------------ T05 to T13: guards

def t05_guard_a_direct_typo_is_already_invalid_in_the_detail_row():
    for where in ("project", "global"):
        d, env = world(rm(IMPL, "opsu") if where == "project" else {}, rm(IMPL, "opsu") if where == "global" else None)
        data, rows, rj, rt = view(d, env)
        row = rows.get(IMPL, {})
        why = reason_of(row)
        check(f"[CP2-T05] ({where}) requested opsu, effective inherit, source {where}",
              row.get("requested") == "opsu" and row.get("effective") == "inherit" and row.get("source") == where, row)
        check(f"[CP2-T05] ({where}) the reason names opsu as invalid or unknown and lists the valid names",
              re.search(r"\bopsu\b", why) is not None and INVALID.search(why) is not None and has_valid_names(why), why)
        check(f"[CP2-T05] ({where}) the reason says `brief` and the launch guard refuse it",
              "`brief`" in why and re.search(r"launch guard", why) is not None and re.search(r"refuse", why) is not None,
              why)


def t06_guard_nothing_set_every_role_names_its_tier_and_model():
    d, env = world()
    data, rows, rj, rt = view(d, env)
    check("[CP2-T06] `models --json` and plain `models` exit 0",
          rj.returncode == 0 and rt.returncode == 0, (rj.returncode, rt.returncode))
    bad = []
    for role, tier in sorted(TIERS.items()):
        row = rows.get(role, {})
        want = OLD_ROLES[role]
        if not (row.get("requested") == tier and row.get("effective") == want and row.get("source") == "builtin"
                and row.get("reason") == ""):
            bad.append((role, row))
    check("[CP2-T06] each of the 13 sub-agent roles: requested is its shipped tier, effective is the recorded model, "
          "source builtin, empty reason", not bad and len(rows) >= 13, bad[:3])
    lines = rt.stdout.splitlines()
    bad = [role for role, tier in sorted(TIERS.items())
           if f"  {role:<28} asked {tier}; uses {SESSION if OLD_ROLES[role] == 'inherit' else OLD_ROLES[role]}; "
              f"from builtin" not in lines]
    check("[CP2-T06] each role's plain line reads 'asked <tier>; uses <model or your session's model>; from builtin'",
          not bad, bad)
    check("[CP2-T06] the roles map equals the recorded pre-change values exactly",
          (data or {}).get("roles") == OLD_ROLES, (data or {}).get("roles"))
    check("[CP2-T06] the spawn table text equals the recorded pre-change header and thirteen rows exactly",
          lines[:14] == old_spawn_lines(), lines[:14])


def t07_guard_a_host_without_a_per_agent_model_still_names_its_tier():
    d, env = world(host="codex")
    data, rows, rj, rt = view(d, env)
    impl, tw = rows.get(IMPL, {}), rows.get(TW, {})
    check("[CP2-T07] the host is openai and `models --json` exits 0",
          rj.returncode == 0 and (data or {}).get("host") == "openai", (rj.returncode, (data or {}).get("host")))
    check("[CP2-T07] implementer: requested strong, effective inherit, empty reason",
          impl.get("requested") == "strong" and impl.get("effective") == "inherit" and impl.get("reason") == "", impl)
    check("[CP2-T07] test-writer: requested standard, effective inherit",
          tw.get("requested") == "standard" and tw.get("effective") == "inherit", tw)
    check("[CP2-T07] test-writer's reason says the host (openai) can't choose a model per sub-agent",
          re.search(r"host \(openai\)[^.]*can(?:no|['’])t choose a model per sub-agent", reason_of(tw)) is not None,
          reason_of(tw))
    check("[CP2-T07] the roles map is all inherit",
          (data or {}).get("roles") == {k: "inherit" for k in OLD_ROLES}, (data or {}).get("roles"))


def t08_guard_a_choice_that_cannot_apply_falls_to_the_default_and_is_not_invalid():
    d, env = world(AGENT_OTHER)
    data, rows, rj, rt = view(d, env)
    row = rows.get(TW, {})
    why = reason_of(row)
    check("[CP2-T08] requested openai/gpt-5, effective sonnet",
          row.get("requested") == "openai/gpt-5" and row.get("effective") == "sonnet", row)
    check("[CP2-T08] the reason says a sub-agent can't use another company's provider",
          re.search(r"can(?:no|['’])t use a model from another company", why) is not None, why)
    check("[CP2-T08] the reason has no 'invalid' or 'unknown' wording", INVALID.search(why) is None, why)
    block = asked_block(rt.stdout, TW)
    check("[CP2-T08] the plain text says it uses sonnet and has no invalid wording for the role",
          bool(block) and "; uses sonnet;" in block[0] and INVALID.search("\n".join(block)) is None, block)


def t09_guard_a_choice_that_cannot_apply_over_a_valid_tier_runs_on_that_tier():
    d, env = world(AGENT_OTHER, rm(TW, "light"))
    data, rows, rj, rt = view(d, env)
    row = rows.get(TW, {})
    why = reason_of(row)
    check("[CP2-T09] requested openai/gpt-5, effective haiku", row.get("requested") == "openai/gpt-5"
          and row.get("effective") == "haiku", row)
    check("[CP2-T09] the reason is about the agents choice only: it says it can't apply and has no invalid wording",
          CANT_APPLY.search(why) is not None and INVALID.search(why) is None, why)
    block = asked_block(rt.stdout, TW)
    check("[CP2-T09] the plain text says it uses haiku", bool(block) and "; uses haiku;" in block[0], block)


def t10_guard_a_full_model_id_is_never_called_invalid():
    d, env = world(rm(TW, "claude-opus-4-1"))
    data, rows, rj, rt = view(d, env)
    row = rows.get(TW, {})
    why = reason_of(row)
    check("[CP2-T10] (direct) effective is claude-opus-4-1 as written, source project",
          row.get("effective") == "claude-opus-4-1" and row.get("source") == "project", row)
    check("[CP2-T10] (direct) the reason keeps 'passed on as written and the host may refuse it' and has no 'invalid'",
          "passed on as written" in why and re.search(r"host may refuse it", why) is not None
          and INVALID_ONLY.search(why) is None, why)
    check("[CP2-T10] (direct) no plain line for the role says invalid",
          INVALID_ONLY.search("\n".join(asked_block(rt.stdout, TW) + [spawn_rows(rt.stdout).get(TW, "")])) is None,
          asked_block(rt.stdout, TW))
    d, env = world(AGENT_OTHER, rm(TW, "claude-opus-4-1"))
    data, rows, rj, rt = view(d, env)
    row = rows.get(TW, {})
    check("[CP2-T10] (below a choice that cannot apply) effective is claude-opus-4-1 as written",
          row.get("requested") == "openai/gpt-5" and row.get("effective") == "claude-opus-4-1", row)
    check("[CP2-T10] (below a choice that cannot apply) there is no 'invalid' wording in the row or the plain text",
          INVALID_ONLY.search(reason_of(row)) is None
          and INVALID_ONLY.search("\n".join(asked_block(rt.stdout, TW) + [spawn_rows(rt.stdout).get(TW, "")])) is None,
          (reason_of(row), asked_block(rt.stdout, TW)))
    check("[CP2-T10] (below a choice that cannot apply) both calls exit 0",
          rj.returncode == 0 and rt.returncode == 0, (rj.returncode, rt.returncode))


def t11_guard_a_valid_project_value_over_a_machine_wide_typo_is_in_use():
    d, env = world(rm(TW, "light"), rm(TW, "sonet"))
    data, rows, rj, rt = view(d, env)
    row = rows.get(TW, {})
    check("[CP2-T11] requested light, effective haiku, source project, empty reason",
          row.get("requested") == "light" and row.get("effective") == "haiku" and row.get("source") == "project"
          and row.get("reason") == "", row)
    block = asked_block(rt.stdout, TW)
    check("[CP2-T11] the plain text says asked light, uses haiku, from project, with no reason lines",
          bool(block) and "asked light; uses haiku; from project" in block[0] and len(block) == 1, block)
    check("[CP2-T11] nothing in the plain text or the notes marks sonet invalid for this role, and the spawn row is haiku",
          "sonet" not in "\n".join(block) and "sonet" not in rt.stderr and spawn_rows(rt.stdout).get(TW) == "haiku",
          (spawn_rows(rt.stdout).get(TW), rt.stderr[:200]))


def t12_guard_what_launches_is_unchanged_loading_is_tolerant_and_the_repair_works():
    d, env = al.building(AGENT_OTHER)
    cl.put_global(env, rm(TW, "sonet"))
    gpath = str(cl.global_path(env))
    before = disk(d, env)
    for args in (("config",), ("status",), ("models",)):
        r = run(d, env, *args)
        check(f"[CP2-T12] `{args[0]}` exits 0 and prints no traceback with the typo present",
              r.returncode == 0 and "Traceback" not in text(r), (r.returncode, text(r)[:200]))
    check("[CP2-T12] config, status and models changed no settings file", disk(d, env) == before)
    rb = al.brief(d, env, "test-writer")
    out = text(rb)
    check("[CP2-T12] `brief` refuses (non-zero), naming the machine-wide file, role_models.ratchet-test-writer, sonet and "
          "the valid names",
          rb.returncode != 0 and gpath in out and f"role_models.{TW}" in out and "sonet" in out and has_valid_names(out),
          (rb.returncode, out[:300]))
    check("[CP2-T12] `brief` printed no `Launch with model: sonet` line", f"{al.LAUNCH_LINE}sonet" not in rb.stdout)
    fix = run(d, env, "config", "--global", "--unset", f"role_models.{TW}")
    check("[CP2-T12] the repair `config --global --unset role_models.ratchet-test-writer` exits 0",
          fix.returncode == 0, text(fix)[:200])
    data, rows, rj, rt = view(d, env)
    row = rows.get(TW, {})
    check("[CP2-T12] after the unset models shows ratchet-test-writer effective sonnet and no invalid wording",
          row.get("effective") == "sonnet" and INVALID_ONLY.search(reason_of(row)) is None
          and "sonet" not in json.dumps(row), row)
    check("[CP2-T12] after the unset the plain text no longer mentions sonet", "sonet" not in text(rt), text(rt)[:200])
    cl.put_global(env, rm(TW, "light"))
    ok = al.brief(d, env, "test-writer")
    check("[CP2-T12] with role_models.ratchet-test-writer = light `brief` runs and prints exactly one "
          "`Launch with model: haiku` line",
          ok.returncode == 0 and al.line_count(ok.stdout, al.LAUNCH_LINE + "haiku") == 1
          and ok.stdout.count("Launch with model") == 1, (ok.returncode, text(ok)[-300:]))


def t13_guard_a_valid_tier_is_shown_as_in_use():
    d, env = world(rm(IMPL, "light"))
    data, rows, rj, rt = view(d, env)
    row = rows.get(IMPL, {})
    check("[CP2-T13] implementer: requested light, effective haiku, source project, empty reason",
          row.get("requested") == "light" and row.get("effective") == "haiku" and row.get("source") == "project"
          and row.get("reason") == "", row)
    spawn = spawn_rows(rt.stdout)
    check("[CP2-T13] the spawn row for implementer reads haiku", spawn.get(IMPL) == "haiku", spawn.get(IMPL))
    check("[CP2-T13] the other twelve spawn rows equal the defaults", len(spawn) == 13 and others_unchanged(spawn, IMPL),
          spawn)
    check("[CP2-T13] the other twelve detail rows are the defaults (builtin, empty reason)",
          all(rows.get(r, {}).get("source") == "builtin" and rows.get(r, {}).get("reason") == ""
              and rows.get(r, {}).get("effective") == m for r, m in OLD_ROLES.items() if r != IMPL), "")


# ------------------------------------------------------------------------------------------------ T14: registration

def t14_new_file_is_listed_in_readme_and_the_inventory_stays_consistent():
    me_src = Path(__file__).read_text()
    lines = [ln.strip() for ln in README.read_text().splitlines()]
    check("[CP2-T14] the README 'Running the tests' list holds the line `python3 tests/test_ffl_role_report.py`",
          "python3 tests/test_ffl_role_report.py" in lines, None)
    # The live pattern of tests/test_modularize_inventory.py, assembled from pieces so this check never matches itself.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(me_src) or src_pat.search(me_src))
    inv = INVENTORY.read_text()
    check("[CP2-T14] this file reads no engine source, or else it is named in the test inventory",
          (not scans) or "test_ffl_role_report.py" in inv, f"scans={scans}")
    check("[CP2-T14] the earlier CP1 file's inventory entry is still there (the inventory stays consistent)",
          "test_ffl_wording.py" in inv, None)


TESTS = [
    ("CP2-T01", t01_typo_below_a_choice_that_cannot_apply_is_invalid_in_the_detail_row),
    ("CP2-T02", t02_plain_text_and_note_never_present_the_typo_as_the_model_in_use),
    ("CP2-T03", t03_a_role_whose_default_is_the_sessions_model_hides_the_typo_too),
    ("CP2-T04", t04_a_direct_typo_is_not_a_spawn_model_in_either_settings_file),
    ("CP2-T05", t05_guard_a_direct_typo_is_already_invalid_in_the_detail_row),
    ("CP2-T06", t06_guard_nothing_set_every_role_names_its_tier_and_model),
    ("CP2-T07", t07_guard_a_host_without_a_per_agent_model_still_names_its_tier),
    ("CP2-T08", t08_guard_a_choice_that_cannot_apply_falls_to_the_default_and_is_not_invalid),
    ("CP2-T09", t09_guard_a_choice_that_cannot_apply_over_a_valid_tier_runs_on_that_tier),
    ("CP2-T10", t10_guard_a_full_model_id_is_never_called_invalid),
    ("CP2-T11", t11_guard_a_valid_project_value_over_a_machine_wide_typo_is_in_use),
    ("CP2-T12", t12_guard_what_launches_is_unchanged_loading_is_tolerant_and_the_repair_works),
    ("CP2-T13", t13_guard_a_valid_tier_is_shown_as_in_use),
    ("CP2-T14", t14_new_file_is_listed_in_readme_and_the_inventory_stays_consistent),
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
