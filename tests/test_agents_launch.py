#!/usr/bin/env python3
"""
CP6 tests: sub-agents launch on the chosen model.   python3 tests/test_agents_launch.py

When a developer picks a model for a sub-agent role (`agents.<role>`, or the older `role_models`, in the machine-wide
file or the project's config.json; the project's value wins), Ratchet makes the choice real instead of decorative:
`brief CPn --role <role>` carries one line `Launch with model: <model>` (the model part of provider/model), and on
Claude Code the launch guard (`hook guard --host claude`) refuses, during a build, a launch of that `ratchet-<role>`
sub-agent that leaves the model out or names a different one, with a message that names the right model, says to
relaunch with it and points at the setting that chose it. Nothing is refused when no model was chosen for that role
(the built-in defaults stay advisory), on other hosts, for non-Ratchet sub-agents, outside a build, or when the config
cannot be read (that fails open, and the hook crash is recorded like any other).

Real model-name validity is enforced here too (the CP5 director ruling) at the points where work starts: a misspelled
sub-agent model is refused with the valid names; a model an outside-reviewer provider declares it does not offer is refused before any reviewer
launches and before any test request is spent, while a provider that declares nothing passes the model through and
`models` says "not verified"; a review that fails partway on a chosen model marks the provider unavailable for that
model only, so removing or changing the choice frees the reviewer at once; and a hand-written `launch_model` key in a
providers entry (an in-memory key only the choice logic sets) can never launch or report a model nobody chose.

Design assumptions made where the production API does not exist yet (each is stated again at its case):
  * the line the brief prints is exactly `Launch with model: <model>` on a line of its own;
  * a refused launch exits 2 and its message names the model, contains `agents.<role>` for a role chosen through
    `agents`, and says to relaunch;
  * a provider declares the models it offers with a `models` list in its providers entry;
  * "not verified" is the plain phrase `models` uses for a model nobody could check;
  * loading stays tolerant (director ruling): a misspelled sub-agent model, or an outside-reviewer model its provider
    does not offer, never stops `config`, `models`, `status` or the repair commands; `models` shows it as invalid or
    not offered, never as used. The refusal is at the work-start points (`brief`, the launch guard, `reviewers`,
    `plan-submit`) and at `config --set`, naming the file, `agents.<role>`, the model and the valid or offered names.

Everything runs in-process through the engine's own entry points (standard 30): the engine module is loaded once per
process by tests/trust_helpers.py, each case builds its own throwaway git project with a private per-user Ratchet
home, and the outside-reviewer CLIs are stubs on a private PATH (tests/test_agents_reviewers.py's). external_review.py
is loaded by path and run in-process against the shared engine. No network, no real provider CLI, nothing is spent.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP6-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402
import trust_helpers as th                              # noqa: E402
import test_config_layers as cl                         # noqa: E402  (in-process helpers: project(), run(), ...)
import test_agents_reviewers as ar                      # noqa: E402  (stub CLIs, approved_world(), resolve(), ...)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP6-[TX]\d+)\]")


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

NOMODEL = object()                 # a launch whose tool_input carries no `model` field at all
LAUNCH_LINE = "Launch with model: "
RELAUNCH = re.compile(r"re-?launch|launch (?:it |this |the \S+ )?(?:again|with|on)", re.I)


def building(proj=None, glob=None, host="claude"):
    """A throwaway git project with one checkpoint approved into building (phase `building`), `proj` written as the
    project's config.json and `glob` as the machine-wide file BEFORE the plan is approved (agents is a locked setting).
    Returns (dir, env)."""
    env = th.make_env(host)
    if glob is not None:
        cl.put_global(env, glob)
    d = Path(_tmp.mkdtemp(prefix="ratchet-agents-launch-"))
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
    if proj is not None:
        cl.project_path(d).write_text(json.dumps(proj))

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
    return d, env


def brief(d, env, role="implementer"):
    return cl.run(d, env, "brief", "CP1", "--role", role)


def prompt_for(d, env, role="implementer"):
    """A launch prompt that carries the plan's objective (brief's own output), so the objective check never fires and
    only the model check can refuse the launch."""
    r = brief(d, env, role)
    assert r.returncode == 0, cl.text(r)
    return r.stdout + "\n\nGo do the work for checkpoint CP1 now."


def guard(d, env, subagent, prompt, model=NOMODEL, host="claude", tool="Agent", extra=None):
    """One `hook guard --host <host>` call with a sub-agent launch payload (Claude Code sends tool_name Agent on
    current builds, Task on older ones)."""
    ti = {"subagent_type": subagent, "prompt": prompt, "description": "work"}
    if model is not NOMODEL:
        ti["model"] = model
    payload = {"cwd": str(d), "tool_name": tool, "tool_input": ti}
    payload.update(extra or {})
    return cl.run(d, env, "hook", "guard", "--host", host, stdin=json.dumps(payload))


def crash_recorded(d):
    errlog = Path(d) / ".ratchet" / "hook-errors.log"
    crashdir = Path(d) / ".ratchet" / "hook-crashes"
    return (errlog.exists() and errlog.read_text().strip() != "") or (crashdir.is_dir() and any(crashdir.iterdir()))


def read_proj(d):
    return json.loads(cl.project_path(d).read_text())


def write_proj(d, cfg):
    cl.project_path(d).write_text(json.dumps(cfg))


def config_bytes(d, env):
    """The settings files and the run's state, byte for byte (nothing a launch check does may change them)."""
    out = {}
    for label, p in (("project", cl.project_path(d)), ("global", cl.global_path(env)),
                     ("state", Path(d) / ".ratchet/runs/f/state.json")):
        out[label] = p.read_bytes() if p.exists() else None
    return out


def models_json(d, env):
    r = cl.run(d, env, "models", "--json")
    try:
        return json.loads(r.stdout), r
    except ValueError:
        return None, r


def detail_row(data, role):
    return next((e for e in (data or {}).get("detail", []) if e.get("role") == role), {})


def line_count(text, line):
    return sum(1 for l in text.splitlines() if l.strip() == line)


# ------------------------------------------------------------------------------------------------ cases

def t01_brief_names_the_chosen_model():
    d, env = building({"agents": {"implementer": "sonnet"}})
    r = brief(d, env, "implementer")
    out = r.stdout
    check("[CP6-T01] `brief CP1 --role implementer` runs", r.returncode == 0, cl.text(r)[-300:])
    check("[CP6-T01] the output has exactly one line `Launch with model: sonnet`",
          line_count(out, LAUNCH_LINE + "sonnet") == 1 and out.count("Launch with model") == 1, out[-400:])
    m = cl.engine()
    check("[CP6-T01] the usual objective text and the role's rules are still there",
          "Who it's for:" in out and m.ROLE_BRIEFS["implementer"] in out, out[-300:])
    for role in ("fixer", "test-writer"):
        r2 = brief(d, env, role)
        check(f"[CP6-T01] the same call for --role {role} (no choice) has no `Launch with model` line",
              r2.returncode == 0 and "Launch with model" not in r2.stdout, cl.text(r2)[-300:])


def t02_no_choice_brief_unchanged():
    d, env = building()
    base = brief(d, env, "implementer")
    out = base.stdout
    m = cl.engine()
    check("[CP6-T02] with no setting anywhere the brief has no `Launch with model` line",
          base.returncode == 0 and "Launch with model" not in out, cl.text(base)[-300:])
    lines = out.splitlines()
    check("[CP6-T02] it is the unchanged shape: header line, the objective, then the role's rules and nothing after",
          bool(lines) and lines[0].startswith("CP1: brief for the implementer") and "Objective" in out
          and out.endswith(m.ROLE_BRIEFS["implementer"] + "\n"), out[-300:])
    base_cfg = read_proj(d) if cl.project_path(d).exists() else {}
    variants = (("an empty agents setting", dict(base_cfg, agents={})),
                ("an unrelated setting", dict(base_cfg, related_tests_max=4)),
                ("a choice for another role", dict(base_cfg, agents={"fixer": "opus"})),
                ("a choice for the outside reviewer", dict(base_cfg, agents={"reviewer": "codex"})))
    for label, cfg in variants:
        write_proj(d, cfg)
        got = brief(d, env, "implementer")
        check(f"[CP6-T02] with {label} the implementer brief is byte for byte the same",
              got.returncode == 0 and got.stdout == out, cl.text(got)[-300:])
    write_proj(d, base_cfg)
    cl.put_global(env, {})
    got = brief(d, env, "implementer")
    check("[CP6-T02] with an empty machine-wide file it is byte for byte the same",
          got.returncode == 0 and got.stdout == out, cl.text(got)[-300:])


def t03_global_choice_reaches_the_brief_and_project_overrides():
    d, env = building(glob={"agents": {"test-writer": "claude/haiku"}})
    first = brief(d, env, "test-writer")
    check("[CP6-T03] a model chosen in the global file reaches the brief (the model part of provider/model)",
          first.returncode == 0 and line_count(first.stdout, LAUNCH_LINE + "haiku") == 1
          and first.stdout.count("Launch with model") == 1, cl.text(first)[-400:])
    cfg = read_proj(d) if cl.project_path(d).exists() else {}
    write_proj(d, dict(cfg, agents={"test-writer": "opus"}))
    second = brief(d, env, "test-writer")
    check("[CP6-T03] after the project sets its own value the brief carries that one instead",
          second.returncode == 0 and line_count(second.stdout, LAUNCH_LINE + "opus") == 1
          and second.stdout.count("Launch with model") == 1 and "haiku" not in second.stdout,
          cl.text(second)[-400:])


def t04_wrong_model_is_refused_with_the_right_name():
    d, env = building({"agents": {"implementer": "sonnet"}})
    prompt = prompt_for(d, env)
    before = config_bytes(d, env)
    for tool in ("Agent", "Task"):
        r = guard(d, env, "ratchet-implementer", prompt, "opus", tool=tool)
        out = cl.text(r)
        check(f"[CP6-T04] ({tool}) a ratchet-implementer launch on opus is refused (exit 2)", r.returncode == 2, out[-300:])
        check(f"[CP6-T04] ({tool}) the message names sonnet and says to relaunch with it",
              "sonnet" in out and RELAUNCH.search(out) is not None, out[-400:])
        check(f"[CP6-T04] ({tool}) the message points at the agents line that set it", "agents.implementer" in out,
              out[-400:])
    check("[CP6-T04] no state or config file was modified", config_bytes(d, env) == before)


def t05_launch_on_the_chosen_model_is_allowed():
    d, env = building({"agents": {"implementer": "sonnet"}})
    prompt = prompt_for(d, env)
    for sub in ("ratchet-implementer", "ratchet-loop:ratchet-implementer"):
        r = guard(d, env, sub, prompt, "sonnet")
        out = cl.text(r)
        check(f"[CP6-T05] a {sub} launch on sonnet exits 0", r.returncode == 0, out[-300:])
        check(f"[CP6-T05] a {sub} launch on sonnet prints no refusal text", "RATCHET GUARD" not in out and out.strip() == "",
              out[-300:])


def t06_launch_without_a_model_is_refused():
    d, env = building({"agents": {"implementer": "sonnet"}})
    prompt = prompt_for(d, env)
    r = guard(d, env, "ratchet-implementer", prompt, NOMODEL)
    out = cl.text(r)
    check("[CP6-T06] a launch with no `model` field is refused (exit 2) when a model was chosen", r.returncode == 2, out[-300:])
    check("[CP6-T06] the message names sonnet", "sonnet" in out, out[-400:])
    check("[CP6-T06] it explains that an unset model would inherit the parent session's model",
          re.search(r"inherit|session", out, re.I) is not None, out[-400:])


def t07_no_choice_every_launch_works():
    d, env = building()
    prompt = prompt_for(d, env)
    for label, model in (("opus", "opus"), ("haiku", "haiku"), ("no model", NOMODEL)):
        r = guard(d, env, "ratchet-implementer", prompt, model)
        check(f"[CP6-T07] with no choice a ratchet-implementer launch on {label} exits 0 (defaults stay advisory)",
              r.returncode == 0, cl.text(r)[-300:])


def t08_older_role_models_key_is_enforced_the_same_way():
    d, env = building({"role_models": {"ratchet-implementer": "opus"}})
    prompt = prompt_for(d, env)
    r = guard(d, env, "ratchet-implementer", prompt, "sonnet")
    out = cl.text(r)
    check("[CP6-T08] a launch on sonnet is refused (exit 2) when role_models chose opus", r.returncode == 2, out[-300:])
    check("[CP6-T08] the message names opus", "opus" in out, out[-300:])
    ok = guard(d, env, "ratchet-implementer", prompt, "opus")
    check("[CP6-T08] a launch on opus exits 0", ok.returncode == 0, cl.text(ok)[-300:])


def t09_project_level_beats_global_at_launch():
    d, env = building({"agents": {"implementer": "opus"}}, glob={"agents": {"implementer": "haiku"}})
    prompt = prompt_for(d, env)
    r = guard(d, env, "ratchet-implementer", prompt, "haiku")
    out = cl.text(r)
    check("[CP6-T09] a launch on the global value (haiku) is refused (exit 2)", r.returncode == 2, out[-300:])
    check("[CP6-T09] the message names the project's value, opus", "opus" in out, out[-300:])
    ok = guard(d, env, "ratchet-implementer", prompt, "opus")
    check("[CP6-T09] a launch on the project's value (opus) exits 0", ok.returncode == 0, cl.text(ok)[-300:])
    d2, env2 = building(glob={"agents": {"implementer": "haiku"}})
    p2 = prompt_for(d2, env2)
    r2 = guard(d2, env2, "ratchet-implementer", p2, "opus")
    check("[CP6-T09] a model chosen only in the global file is enforced too: another model is refused naming haiku",
          r2.returncode == 2 and "haiku" in cl.text(r2), cl.text(r2)[-300:])


def t10_other_hosts_are_never_refused():
    d, env = building({"agents": {"implementer": "sonnet"}})
    prompt = prompt_for(d, env)
    for host, extra in (("codex", {"turn_id": "turn-1"}), ("gemini", None), ("copilot", None)):
        for model in ("opus", NOMODEL):
            r = guard(d, env, "ratchet-implementer", prompt, model, host=host, tool="Task", extra=extra)
            check(f"[CP6-T10] --host {host}: a sub-agent launch on {'opus' if model is not NOMODEL else 'no model'} "
                  "exits 0", r.returncode == 0, cl.text(r)[-300:])


def t11_non_ratchet_and_not_building_are_not_checked():
    d, env = building({"agents": {"implementer": "sonnet"}})
    r = guard(d, env, "general-purpose", "Explore the repository and summarize it.", "opus")
    check("[CP6-T11] a general-purpose sub-agent on opus exits 0", r.returncode == 0, cl.text(r)[-300:])
    r = guard(d, env, "Explore", "Look around.", "opus")
    check("[CP6-T11] an Explore sub-agent on opus exits 0", r.returncode == 0, cl.text(r)[-300:])
    d2, env2 = cl.project({"agents": {"implementer": "sonnet"}})     # a run still in planning
    r2 = guard(d2, env2, "ratchet-implementer", "Implement it.", "opus")
    check("[CP6-T11] a ratchet-implementer launch on opus while the phase is planning exits 0",
          r2.returncode == 0 and cl.text(r2).strip() == "", cl.text(r2)[-300:])


def t12_a_choice_for_one_role_does_not_bind_another():
    d, env = building({"agents": {"implementer": "sonnet"}})
    prompt = prompt_for(d, env, "fixer")
    for sub in ("ratchet-fixer", "ratchet-loop:ratchet-fixer"):
        r = guard(d, env, sub, prompt, "opus")
        check(f"[CP6-T12] a {sub} launch on opus exits 0 (only the implementer has a choice)", r.returncode == 0,
              cl.text(r)[-300:])
    r = guard(d, env, "ratchet-fixer", prompt, NOMODEL)
    check("[CP6-T12] a ratchet-fixer launch with no model exits 0", r.returncode == 0, cl.text(r)[-300:])


def t13_unreadable_config_never_blocks_a_launch():
    d, env = building({"agents": {"implementer": "sonnet"}})
    prompt = prompt_for(d, env)
    check("[CP6-T13] (setup) no hook crash is recorded before the damage", not crash_recorded(d))
    cl.project_path(d).write_text("{not valid json at all, this is garbage")
    r = guard(d, env, "ratchet-implementer", prompt, "opus")
    check("[CP6-T13] a damaged project config.json fails the guard open (exit 0): the launch is allowed",
          r.returncode == 0, cl.text(r)[-300:])
    check("[CP6-T13] the crash is recorded like any other hook crash (hook-errors.log or hook-crashes/), not a silent pass",
          crash_recorded(d))
    d2, env2 = building({"agents": {"implementer": "sonnet"}})
    prompt2 = prompt_for(d2, env2)
    cl.global_path(env2).parent.mkdir(parents=True, exist_ok=True)
    cl.global_path(env2).write_text("{not valid json either")
    r2 = guard(d2, env2, "ratchet-implementer", prompt2, "opus")
    check("[CP6-T13] a damaged machine-wide file fails the guard open (exit 0) too", r2.returncode == 0, cl.text(r2)[-300:])
    check("[CP6-T13] and that crash is recorded as well", crash_recorded(d2))


def _row_text(row):
    return json.dumps(row)


def t14_view_says_a_host_that_cannot_pick_cannot_apply_the_choice():
    for host, company in (("codex", "openai"), ("gemini", "google")):
        d, env = cl.project({"agents": {"implementer": "sonnet"}})
        env = dict(env, RATCHET_HOST_PROVIDER=th.VENDOR[host])
        stubs = ar.Stubs(env, ("codex", "gemini", "opencode", "claude"))
        before = config_bytes(d, env)
        data, r = models_json(d, env)
        row = detail_row(data, "ratchet-implementer")
        why = str(row.get("reason", ""))
        check(f"[CP6-T14] ({host}) `models --json` runs", r.returncode == 0 and isinstance(data, dict), cl.text(r)[-300:])
        check(f"[CP6-T14] ({host}) the implementer entry asks for sonnet", row.get("requested") == "sonnet", row)
        check(f"[CP6-T14] ({host}) its real model differs from the request", row.get("effective") not in (None, "sonnet"), row)
        check(f"[CP6-T14] ({host}) it says plainly the choice cannot be applied on this host",
              re.search(r"can(?:no|')t", why) is not None and (company in why or host in why), row)
        plain = cl.run(d, env, "models")
        lines = [l for l in plain.stdout.splitlines() if "ratchet-implementer" in l]
        check(f"[CP6-T14] ({host}) the plain `models` run shows the implementer's real model as not sonnet",
              plain.returncode == 0 and lines != [] and not any(re.search(r"\buses sonnet\b|\bsonnet$", l) for l in lines),
              plain.stdout[-600:])
        check(f"[CP6-T14] ({host}) the plain text says why: the host cannot pick a model per sub-agent",
              re.search(r"can(?:no|')t choose a model|can(?:no|')t apply", plain.stdout) is not None, plain.stdout[-600:])
        check(f"[CP6-T14] ({host}) no provider CLI was contacted and nothing was written",
              stubs.calls() == [] and config_bytes(d, env) == before, stubs.calls())


def t15_on_claude_code_the_view_says_the_choice_is_applied():
    d, env = cl.project({"agents": {"implementer": "sonnet"}})
    data, r = models_json(d, env)
    row = detail_row(data, "ratchet-implementer")
    check("[CP6-T15] `models --json` runs", r.returncode == 0 and isinstance(data, dict), cl.text(r)[-300:])
    check("[CP6-T15] implementer requested and real are both sonnet, from the project, with no cannot-apply reason",
          row.get("requested") == "sonnet" and row.get("effective") == "sonnet" and row.get("source") == "project"
          and row.get("reason") == "", row)
    d0, env0 = cl.project({"related_tests_max": 4})
    base, r0 = models_json(d0, env0)
    check("[CP6-T15] the legacy keys host, roles and alternates keep their shape and values",
          isinstance(base, dict) and data.get("host") == base.get("host") == "anthropic"
          and data.get("alternates") == base.get("alternates") == ["sonnet", "opus"]
          and {k: v for k, v in data["roles"].items() if k != "ratchet-implementer"}
          == {k: v for k, v in base["roles"].items() if k != "ratchet-implementer"}
          and data["roles"]["ratchet-implementer"] == "sonnet", (data.get("roles"), cl.text(r0)[-200:]))


def t16_readme_lists_the_new_file():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("### Running the tests", 1)[-1]
    blocks = re.findall(r"```[^\n]*\n(.*?)```", section, re.S)
    home = [b for b in blocks if re.search(r"^python3 tests/test_brief_guard\.py$", b, re.M)]
    check("[CP6-T16] the 'Running the tests' block that lists python3 tests/test_brief_guard.py exists", len(home) == 1, len(home))
    mine = re.compile(r"^python3 tests/test_agents_launch\.py$", re.M)
    check("[CP6-T16] that same block lists python3 tests/test_agents_launch.py exactly once",
          len(home) == 1 and len(mine.findall(home[0])) == 1, len(mine.findall(home[0])) if home else None)


def t17_inventory_registers_the_new_file():
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    kind1 = inv.split("## Kind 1", 1)[-1].split("## Kind 2", 1)[0]
    bullets = re.findall(r"(?m)^- `([^`]+)`", kind1)
    stated = re.search(r"\): (\d+) files", kind1)
    module_load = re.compile("spec_from_file" + "_location")
    source_text = re.compile("ENGINE\\.read_" + "text|ast\\.pa" + "rse|ast\\.wa" + "lk|getsou" + "rce")
    mine = Path(__file__).read_text()
    check("[CP6-T17] the inventory registers tests/test_agents_launch.py", "test_agents_launch.py" in inv)
    check("[CP6-T17] it sits under the 'Kind 1' (module load only) heading, where test_brief_guard.py is",
          "test_agents_launch.py" in bullets and "test_brief_guard.py" in bullets, bullets[-3:])
    check("[CP6-T17] the Kind 1 heading's file count equals the entries listed under it",
          stated is not None and int(stated.group(1)) == len(bullets), (stated and stated.group(1), len(bullets)))
    check("[CP6-T17] the scanner classifies this file as module-load only (matches that pattern, not the other)",
          module_load.search(mine) is not None and source_text.search(mine) is None)


# ---- T18-T21: real model-name validity, the partway failure mark, and the hand-written launch_model key

VALID_ALIASES = ("sonnet", "opus", "haiku")


def _typo_world(level, value="sonet"):
    """A planning-phase project whose `level` file (project or machine-wide) already holds the misspelled model. The bad
    value is written AFTER the world is made, so only the commands under test see it. Returns (dir, env, file path)."""
    d, env = cl.project({"related_tests_max": 4})
    if level == "project":
        write_proj(d, {"related_tests_max": 4, "agents": {"implementer": value}})
        return d, env, str(cl.project_path(d))
    cl.put_global(env, {"agents": {"implementer": value}})
    return d, env, str(cl.global_path(env))


INVALID_WORDS = re.compile(r"invalid|unknown|not a model|not a valid|not recogni[sz]ed|isn't a (?:known )?model", re.I)


def t18_misspelled_sub_agent_model_is_refused_before_launch():
    # LOAD STAYS TOLERANT: with the typo in the project file, and separately in the machine-wide file, the settings
    # still load, `config`, `models` and `status` run (exit 0) and `models` shows the name as invalid, never as used
    for level in ("project", "global"):
        d, env, where = _typo_world(level)
        before = config_bytes(d, env)
        for args in (("config",), ("status",)):
            r = cl.run(d, env, *args)
            check(f"[CP6-T18] ({level}) `{args[0]}` still runs (exit 0) with the misspelled sonet in the file",
                  r.returncode == 0 and "Traceback" not in cl.text(r), (r.returncode, cl.text(r)[:300]))
        data, rm = models_json(d, env)
        row = detail_row(data, "ratchet-implementer")
        check(f"[CP6-T18] ({level}) `models --json` still runs (exit 0)", rm.returncode == 0 and isinstance(data, dict),
              (rm.returncode, cl.text(rm)[:300]))
        check(f"[CP6-T18] ({level}) `models --json` shows sonet as invalid or unknown and never as the real model",
              row.get("requested") == "sonet" and row.get("effective") != "sonet"
              and INVALID_WORDS.search(_row_text(row)) is not None, row)
        plain = cl.run(d, env, "models")
        lines = [l for l in plain.stdout.splitlines() if "ratchet-implementer" in l]
        check(f"[CP6-T18] ({level}) the plain `models` run exits 0, never says sonet is used, and calls it invalid or unknown",
              plain.returncode == 0 and "sonet" in plain.stdout and INVALID_WORDS.search(plain.stdout) is not None
              and not any(re.search(r"\buses? sonet\b|\bsonet$", l) for l in lines), (plain.returncode, plain.stdout[-500:]))
        check(f"[CP6-T18] ({level}) loading and viewing changed nothing on disk", config_bytes(d, env) == before)
    # repair paths with the typo already present in each file: every one runs and really fixes the file
    d, env, where = _typo_world("project")
    r = cl.run(d, env, "config", "--unset", "agents.implementer")
    check("[CP6-T18] (project) `config --unset agents.implementer` repairs a project file that holds the typo (exit 0)",
          r.returncode == 0 and "implementer" not in (read_proj(d).get("agents") or {}), (r.returncode, cl.text(r)[:300]))
    data, rm = models_json(d, env)
    check("[CP6-T18] (project) after the repair `models` runs and no longer mentions sonet",
          rm.returncode == 0 and "sonet" not in rm.stdout, cl.text(rm)[:300])
    d, env, where = _typo_world("project")
    r = cl.run(d, env, "config", "--set", "agents.implementer=opus")
    check("[CP6-T18] (project) `config --set agents.implementer=opus` replaces the typo in the project file (exit 0)",
          r.returncode == 0 and (read_proj(d).get("agents") or {}).get("implementer") == "opus", (r.returncode, cl.text(r)[:300]))
    d, env, where = _typo_world("global")
    r = cl.run(d, env, "config", "--global", "--unset", "agents.implementer")
    gone = json.loads(cl.global_path(env).read_text()) if cl.global_path(env).exists() else {}
    check("[CP6-T18] (global) `config --global --unset agents.implementer` repairs a global file that holds the typo (exit 0)",
          r.returncode == 0 and "implementer" not in (gone.get("agents") or {}), (r.returncode, cl.text(r)[:300]))
    d, env, where = _typo_world("global")
    r = cl.run(d, env, "config", "--global", "--set", "agents.implementer=opus")
    fixed = json.loads(cl.global_path(env).read_text())
    check("[CP6-T18] (global) `config --global --set agents.implementer=opus` replaces the typo in the global file (exit 0)",
          r.returncode == 0 and (fixed.get("agents") or {}).get("implementer") == "opus", (r.returncode, cl.text(r)[:300]))
    d, env, where = _typo_world("global")
    r1 = cl.run(d, env, "config", "--set", "agents.implementer=haiku")
    r2 = cl.run(d, env, "config", "--unset", "agents.implementer")
    check("[CP6-T18] (global typo) a typo in the global file never blocks a project `--set` or `--unset` (both exit 0)",
          r1.returncode == 0 and r2.returncode == 0 and "implementer" not in (read_proj(d).get("agents") or {}),
          (r1.returncode, cl.text(r1)[:200], r2.returncode, cl.text(r2)[:200]))
    # `config --set` refuses to WRITE the misspelled name, naming the valid names, and leaves the files as they were
    for flags, label in (((), "project"), (("--global",), "global")):
        d, env = cl.project({"related_tests_max": 4})
        before = config_bytes(d, env)
        r = cl.run(d, env, "config", *flags, "--set", "agents.implementer=sonet")
        out = cl.text(r)
        check(f"[CP6-T18] ({label}) `config --set agents.implementer=sonet` refuses to write it, naming sonet and the valid names",
              r.returncode != 0 and "sonet" in out and all(n in out for n in VALID_ALIASES) and "Traceback" not in out,
              (r.returncode, out[:300]))
        check(f"[CP6-T18] ({label}) ... and nothing was written", config_bytes(d, env) == before)
    # the refusal is at the work-start points: `brief` and the launch guard, in a run already building whose setting
    # is later misspelled by hand, in the project file and then in the machine-wide file
    for level in ("project", "global"):
        d, env = building({"agents": {"implementer": "opus"}})
        prompt = prompt_for(d, env)
        good = read_proj(d)
        if level == "project":
            write_proj(d, dict(good, agents={"implementer": "sonet"}))
            where = str(cl.project_path(d))
        else:
            cl.put_global(env, {"agents": {"implementer": "sonet"}})
            write_proj(d, {k: v for k, v in good.items() if k != "agents"})
            where = str(cl.global_path(env))
        for args in (("config",), ("status",), ("models",)):
            r = cl.run(d, env, *args)
            check(f"[CP6-T18] ({level}, building) `{args[0]}` still runs (exit 0) with the typo present",
                  r.returncode == 0, (r.returncode, cl.text(r)[:300]))
        r = guard(d, env, "ratchet-implementer", prompt, "sonet")
        out = cl.text(r)
        check(f"[CP6-T18] ({level}) the launch guard refuses the misspelled model before launch (exit 2), naming the file, "
              f"agents.implementer and the valid names",
              r.returncode == 2 and "sonet" in out and where in out and "agents.implementer" in out
              and all(n in out for n in VALID_ALIASES), (r.returncode, out[:400]))
        rb = brief(d, env, "implementer")
        out = cl.text(rb)
        check(f"[CP6-T18] ({level}) `brief` for that role refuses, naming sonet, the file, agents.implementer and the valid names",
              rb.returncode != 0 and "sonet" in out and where in out and "agents.implementer" in out
              and all(n in out for n in VALID_ALIASES), (rb.returncode, out[:400]))
        check(f"[CP6-T18] ({level}) `brief` printed no `Launch with model: sonet` line", f"{LAUNCH_LINE}sonet" not in rb.stdout,
              rb.stdout[:200])
    # valid names still work: an alias launches, a full id the host table cannot judge is shown as not verified
    d, env = building({"agents": {"implementer": "opus"}})
    prompt = prompt_for(d, env)
    r = guard(d, env, "ratchet-implementer", prompt, "opus")
    check("[CP6-T18] a valid alias (opus) still launches (exit 0)", r.returncode == 0, cl.text(r)[-300:])
    d2, env2 = cl.project({"agents": {"implementer": "claude-opus-4-1"}})
    data2, r2 = models_json(d2, env2)
    row = detail_row(data2, "ratchet-implementer")
    check("[CP6-T18] a full model id the host's table cannot judge is not refused as a typo", r2.returncode == 0 and bool(row),
          cl.text(r2)[:300])
    check("[CP6-T18] ... and `models` shows it as not verified or not used, never as used on faith",
          row.get("effective") != "claude-opus-4-1" or "not verified" in str(row.get("reason", "")).lower(), row)
    check("[CP6-T18] no paid approval was written by any of these paths",
          "paid_ok_at" not in ar.tree_text(d / ".ratchet", env["RATCHET_HOME"], d2 / ".ratchet"))


def reviewable_world(providers, before_approval=None):
    """A throwaway project taken, as tests/test_agents_reviewers.py's approved_world() does, up to the step where
    `reviewers CP1` would open the adversarial round, but WITHOUT calling it. Stub CLIs for codex and gemini are
    installed. `before_approval(d, env)`, when given, runs after `plan-submit` and before the plan is approved, so a
    setting it writes is part of the approved config (the way a user configures their reviewer before running). Returns
    (dir, env, stubs)."""
    env = th.make_env("claude")
    stubs = ar.Stubs(env, ("codex", "gemini"))
    d = Path(_tmp.mkdtemp(prefix="ratchet-agents-launch-rw-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    cl.project_path(d).write_text(json.dumps({"providers": [dict(p) for p in providers], "min_review_seconds": 0}))
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
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = cl.run(d, env, "plan-submit")
    assert r.returncode == 0, cl.text(r)
    if before_approval:
        before_approval(d, env)
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
    return d, env, stubs


def _round_open(d):
    st = json.loads((Path(d) / ".ratchet/runs/f/state.json").read_text())
    return bool((st.get("checkpoints") or {}).get("CP1", {}).get("adv_round"))


def t19_not_offered_reviewer_model_is_refused_before_any_reviewer_launches():
    offered = ["gpt-5.1", "gpt-5.2"]
    prov = {"id": "codex", "preset": "codex", "models": list(offered)}
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    offered_phrase = re.compile(r"not offer|does not offer|doesn't offer|isn't offered", re.I)

    def put_typo(d, env, level):
        if level == "project":
            write_proj(d, dict(read_proj(d), agents={"reviewer": "codex/gpt-9"}))
            return str(cl.project_path(d))
        cl.put_global(env, {"agents": {"reviewer": "codex/gpt-9"}})
        return str(cl.global_path(env))

    for level in ("project", "global"):
        # 1. LOAD STAYS TOLERANT, at the step where a round would open: config, models and status run; models shows the
        # model as not offered by that provider and never as the reviewer's real model
        # the setting is part of the approved config: written after plan-submit and before the plan is approved
        wrote = []
        d, env, stubs = reviewable_world([dict(prov), dict(ar.GEMINI)],
                                         before_approval=lambda d_, e_: wrote.append(put_typo(d_, e_, level)))
        where = wrote[0]
        for args in (("config",), ("status",)):
            r = cl.run(d, env, *args)
            check(f"[CP6-T19] ({level}) `{args[0]}` still runs (exit 0) with the not-offered model in the file",
                  r.returncode == 0 and "Traceback" not in cl.text(r), (r.returncode, cl.text(r)[:300]))
        data, rm = models_json(d, env)
        row = detail_row(data, "reviewer")
        check(f"[CP6-T19] ({level}) `models --json` still runs (exit 0)", rm.returncode == 0 and isinstance(data, dict),
              (rm.returncode, cl.text(rm)[:300]))
        check(f"[CP6-T19] ({level}) `models` says gpt-9 is not offered by codex and never reports it as the reviewer's model",
              "gpt-9" not in ar.s(row.get("effective")) and "gpt-9" in ar.s(row) and offered_phrase.search(ar.s(row)) is not None,
              row)
        # 2. `reviewers CP1 --json` refuses before the round opens or any stub CLI runs
        r = cl.run(d, env, "reviewers", "CP1", "--json")
        out = cl.text(r)
        check(f"[CP6-T19] ({level}) `reviewers` refuses the not-offered model, naming it, the provider and the file",
              r.returncode != 0 and "gpt-9" in out and "codex" in out and where in out and "Traceback" not in out,
              (r.returncode, out[:400]))
        check(f"[CP6-T19] ({level}) `reviewers` names the models the provider does offer", all(m in out for m in offered), out[:400])
        check(f"[CP6-T19] ({level}) no outside CLI ran (no test request spent) and no review round was opened",
              stubs.calls() == [] and not _round_open(d), stubs.calls())
        check(f"[CP6-T19] ({level}) no paid approval was written by the refusal",
              "paid_ok_at" not in ar.tree_text(d / ".ratchet", env["RATCHET_HOME"]))
        # 3. `plan-submit` refuses the same way, in a planning-phase project, before anything is submitted
        d2, env2, stubs2 = ar.world(providers=[dict(prov), dict(ar.GEMINI)])
        rd = d2 / ".ratchet/runs/f"
        (rd / "app-scout.json").write_text("{}")
        (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp]}))
        (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
        where2 = put_typo(d2, env2, level)
        for args in (("config",), ("models", "--json")):
            r = cl.run(d2, env2, *args)
            check(f"[CP6-T19] ({level}, planning) `{args[0]}` still runs (exit 0) with the not-offered model in the file",
                  r.returncode == 0, (r.returncode, cl.text(r)[:300]))
        r = cl.run(d2, env2, "plan-submit")
        out = cl.text(r)
        check(f"[CP6-T19] ({level}) `plan-submit` refuses the not-offered model, naming it, the provider, the file and the offered models",
              r.returncode != 0 and "gpt-9" in out and "codex" in out and where2 in out and "Traceback" not in out
              and all(m in out for m in offered), (r.returncode, out[:400]))
        st = json.loads((rd / "state.json").read_text()) if (rd / "state.json").exists() else {}
        check(f"[CP6-T19] ({level}) nothing was submitted, no round opened and no stub CLI ran",
              st.get("phase") == "planning" and stubs2.calls() == [] and not _round_open(d2), (st.get("phase"), stubs2.calls()))
        # 4. repair: the unset runs and the plan then goes through
        flags = () if level == "project" else ("--global",)
        r = cl.run(d2, env2, "config", *flags, "--unset", "agents.reviewer")
        check(f"[CP6-T19] ({level}) `config --unset agents.reviewer` repairs it (exit 0)",
              r.returncode == 0, (r.returncode, cl.text(r)[:300]))
        r = cl.run(d2, env2, "plan-submit")
        check(f"[CP6-T19] ({level}) after the repair `plan-submit` goes through", r.returncode == 0, cl.text(r)[:300])
    # 5. a model the provider offers is applied: reported, tested and shown
    d2, env2, stubs2, rnd = ar.approved_world({"providers": [dict(prov)], "agents": {"reviewer": "codex/gpt-5.2"}})
    rv = ar.reviewer_entry(rnd, "codex-both")
    check("[CP6-T19] an offered model is applied: the round's reviewer reports gpt-5.2", rv.get("model") == "gpt-5.2", rnd.get("reviewers"))
    check("[CP6-T19] the test request used the offered model", any("gpt-5.2" in a for a in ar.probe_models(stubs2)),
          ar.probe_models(stubs2))
    row2, _ = ar.models_row(d2, env2)
    check("[CP6-T19] `models` shows the offered model as the reviewer's and does not call it unverified",
          "gpt-5.2" in ar.s(row2.get("effective")) and "not verified" not in ar.s(row2).lower(), row2)
    # 6. a provider that declares nothing: the model is passed through, and `models` says it is not verified
    d3, env3, stubs3, rnd3 = ar.approved_world({"providers": [dict(ar.CODEX)], "agents": {"reviewer": "codex/gpt-5.9"}})
    rv3 = ar.reviewer_entry(rnd3, "codex-both")
    check("[CP6-T19] a provider that declares no models still gets the model passed through (reported and tested)",
          rv3.get("model") == "gpt-5.9" and any("gpt-5.9" in a for a in ar.probe_models(stubs3)),
          (rnd3.get("reviewers"), ar.probe_models(stubs3)))
    row3, _ = ar.models_row(d3, env3)
    check("[CP6-T19] `models` says that model is not verified",
          "not verified" in ar.s(row3).lower(), row3)
    check("[CP6-T19] and never claims it verified", re.search(r"(?<!not )verified", ar.s(row3).lower()) is None, row3)
    # 7. a paid provider that declares its models: choosing an offered model never grants the paid OK
    paid = dict(ar.PAID, models=["opencode-go/glm-5.3", "opencode-go/glm-offered"])
    d4, env4, _ = ar.world({"agents": {"reviewer": "zpay/opencode-go/glm-offered"}}, providers=[paid])
    got = ar.resolve(d4, env4, "low")
    check("[CP6-T19] choosing an offered model of a pay-per-use provider does not approve paying for it",
          got.out is not None and not any(i.startswith("zpay") for i in got.ids), (got.ids, got.err[-300:]))
    check("[CP6-T19] and no paid_ok_at exists anywhere afterwards",
          "paid_ok_at" not in ar.tree_text(d4 / ".ratchet", env4["RATCHET_HOME"]) and "paid_ok_at" not in ar.tree_text(d2 / ".ratchet", env2["RATCHET_HOME"]))


_EXT = {}


def external_review_module():
    """external_review.py, loaded by path once and pointed at the shared engine module (so it does not recompile it)."""
    if "m" not in _EXT:
        spec = importlib.util.spec_from_file_location("ratchet_external_review_cp6", ar.EXT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.load_engine = cl.engine
        _EXT["m"] = mod
    return _EXT["m"]


def run_review(d, env, rid, out_path, cp="CP1"):
    """The agent runs one outside reviewer through external_review.py, in-process: returns the call (returncode,
    stdout, stderr)."""
    mod, m = external_review_module(), cl.engine()
    argv0 = sys.argv
    try:
        with th.InProcessCall(m, d, env, cl.RESET_GLOBALS, cl.LOCK_WAIT_CAP) as r:
            sys.argv = ["external_review.py", "--reviewer", rid, "--cp", cp, "--out", str(out_path), "--root", str(d)]
            mod.main()
    finally:
        sys.argv = argv0
    return r


# a stub codex that answers a connectivity check but fails a real review of model-x partway (an error, no findings)
PARTWAY_STUB = ar.STUB.replace('if [ "$kind" = probe ]; then',
                               'if [ "$kind" = review ]; then case "$args" in *model-x*) echo "error: boom" >&2; exit 1;; esac; fi\n'
                               'if [ "$kind" = probe ]; then', 1)
# a stub codex whose connectivity check of model-x fails (the review itself never gets to run)
PROBE_FAIL_STUB = ar.STUB.replace('if [ "$kind" = probe ]; then',
                                  'case "$args" in *model-x*) echo "error: unknown model model-x" >&2; exit 1;; esac\n'
                                  'if [ "$kind" = probe ]; then', 1)


def _freed_after_changes(d, env, tag):
    """The choice is changed to another model, then removed: the provider is available at once each time."""
    base = read_proj(d)
    write_proj(d, dict(base, agents={"reviewer": "codex/model-y"}))
    changed = ar.resolve(d, env, "low")
    row_y, _ = ar.models_row(d, env)
    check(f"[CP6-T20] ({tag}) with the choice changed to model-y, codex reviews again at once",
          changed.out is not None and changed.ids == ["codex-both"] and changed.out[0].get("model") == "model-y",
          (changed.ids, changed.err[-200:]))
    check(f"[CP6-T20] ({tag}) the models view no longer blames model-x", "model-x" not in ar.s(row_y), row_y)
    gone = dict(base)
    gone.pop("agents", None)
    write_proj(d, gone)
    back = ar.resolve(d, env, "low")
    row_n, _ = ar.models_row(d, env)
    check(f"[CP6-T20] ({tag}) with the choice removed, codex reviews again at once on its own model",
          back.out is not None and back.ids == ["codex-both"] and back.out[0].get("model") != "model-x",
          (back.ids, back.err[-200:]))
    check(f"[CP6-T20] ({tag}) and the models view no longer blames model-x", "model-x" not in ar.s(row_n), row_n)
    write_proj(d, base)


def t20_partway_failure_mark_names_the_model():
    # 1. a review of model-x that fails partway, after its test request passed
    def cfg(st):
        (st.bin / "codex").write_text(PARTWAY_STUB.replace("@LOG@", str(st.log)))
        return {"providers": [dict(ar.CODEX)], "agents": {"reviewer": "codex/model-x"}}
    d, env, stubs, data = ar.approved_world(cfg)
    check("[CP6-T20] (setup) the round starts with codex on model-x",
          [(x["id"], x.get("model")) for x in data.get("reviewers", [])] == [("codex-both", "model-x")], data.get("reviewers"))
    out = Path(data["output_dir"]) / "codex-both.json"
    r = run_review(d, env, "codex-both", out)
    check("[CP6-T20] the review script reports the failure and stops (exit 3, UNAVAILABLE)",
          r.returncode == 3 and "UNAVAILABLE" in r.stderr, (r.returncode, r.stderr[-300:]))
    marks = json.loads(ar.unavailable_path(d).read_text()) if ar.unavailable_path(d).exists() else {}
    check("[CP6-T20] the mark for codex-both names the model that was launched (model-x)",
          (marks.get("codex-both") or {}).get("model") == "model-x", marks)
    same = ar.resolve(d, env, "low")
    why = ar.s((same.slot(1).get("reasons") or {}).get("codex-both"))
    check("[CP6-T20] while the choice is still model-x the reviewer stays out, with the reason",
          same.out is not None and "codex-both" not in same.ids and "failed recently" in why, (same.ids, why))
    row, _ = ar.models_row(d, env)
    check("[CP6-T20] and the models view says it failed recently and does not call codex the reviewer in use",
          "failed recently" in ar.s(row.get("reason")) and "codex" not in ar.s(row.get("effective")).lower(), row)
    _freed_after_changes(d, env, "partway failure")
    # 2. the same when the failure was a failed test request of model-x (the review never started)
    def cfg2(st):
        (st.bin / "codex").write_text(PROBE_FAIL_STUB.replace("@LOG@", str(st.log)))
        return {"providers": [dict(ar.CODEX)], "agents": {"reviewer": "codex/model-x"}}
    d2, env2, stubs2, data2 = ar.approved_world(cfg2)
    marks2 = json.loads(ar.unavailable_path(d2).read_text()) if ar.unavailable_path(d2).exists() else {}
    check("[CP6-T20] (test request) the failed test request marked codex-both for model-x",
          (marks2.get("codex-both") or {}).get("model") == "model-x", marks2)
    still = ar.resolve(d2, env2, "low")
    check("[CP6-T20] (test request) with the choice still model-x the reviewer stays out", still.out is not None
          and "codex-both" not in still.ids, still.ids)
    _freed_after_changes(d2, env2, "test request")


def t21_hand_written_launch_model_changes_nothing():
    key = "launch_model"
    own = {"id": "codex", "preset": "codex", "model": "gpt-5", key: "hacked-model"}
    refused_at = {}
    for level in ("project", "global"):
        if level == "project":
            d, env = cl.project({"providers": [dict(own)]})
            where = str(cl.project_path(d))
        else:
            d, env = cl.project({"related_tests_max": 4}, glob={"providers": [dict(own)]})
            where = str(cl.global_path(env))
        ar.Stubs(env, ("codex", "gemini"))
        r = cl.run(d, env, "models", "--json")
        out = cl.text(r)
        refused_at[level] = r.returncode != 0
        if r.returncode != 0:
            check(f"[CP6-T21] ({level}) the hand-written key is refused at load, naming the key and the file",
                  key in out and where in out and "Traceback" not in out, out[:300])
            check(f"[CP6-T21] ({level}) the refusal means no model is reported or launched", "hacked-model" not in r.stdout, r.stdout[:200])
            continue
        # not refused: it must be ignored, so the provider's own model is what is reported and launched
        check(f"[CP6-T21] ({level}) ignored: the models view never reports the hand-written model", "hacked-model" not in out, out[:300])
        got = ar.resolve(d, env, "low")
        check(f"[CP6-T21] ({level}) ignored: the reviewers resolve on the provider's own model (gpt-5)",
              got.out is not None and got.ids == ["codex-both"] and got.out[0].get("model") == "gpt-5",
              (got.ids, got.out and got.out[0].get("model"), got.err[-200:]))
    # a pay-per-use provider approved for its own model: a hand-written key must not change what the approval covers
    paid = dict(ar.PAID_OK, **{key: "opencode-go/glm-evil"})
    dp, envp, _ = ar.world(providers=[paid])
    rp = cl.run(dp, envp, "models", "--json")
    if rp.returncode != 0:
        check("[CP6-T21] (paid) the hand-written key is refused at load, naming the key and the file",
              key in cl.text(rp) and str(cl.project_path(dp)) in cl.text(rp), cl.text(rp)[:300])
    else:
        gotp = ar.resolve(dp, envp, "low")
        check("[CP6-T21] (paid) ignored: the OK the developer gave for the provider's own model still covers it",
              gotp.out is not None and gotp.ids == ["zpay-both"] and gotp.out[0].get("model") == "opencode-go/glm-5.3",
              (gotp.ids, gotp.err[-200:]))
        check("[CP6-T21] (paid) ignored: the hand-written model is not reported", "glm-evil" not in cl.text(rp), cl.text(rp)[:300])
    # a launch: the reviewer script runs the provider's own model, never the hand-written one
    if not refused_at["project"]:
        d3, env3, stubs3, rnd3 = ar.approved_world({"providers": [dict(own)]})
        rv = ar.reviewer_entry(rnd3, "codex-both")
        run = run_review(d3, env3, "codex-both", Path(rnd3["output_dir"]) / "codex-both.json")
        argv = " ".join(c[2] for c in stubs3.calls("codex"))
        check("[CP6-T21] ignored: the round reports gpt-5, the test request and the review ran gpt-5, never the hand-written model",
              rv.get("model") == "gpt-5" and "hacked-model" not in argv and "-m gpt-5" in argv, (rv.get("model"), argv[:300]))
    # the key the choice logic sets in memory still does its job
    d4, env4, stubs4, rnd4 = ar.approved_world({"providers": [dict(ar.CODEX)], "agents": {"reviewer": "codex/gpt-5.1"}})
    check("[CP6-T21] a model chosen with agents.reviewer is still the one launched and reported",
          ar.reviewer_entry(rnd4, "codex-both").get("model") == "gpt-5.1" and any("gpt-5.1" in a for a in ar.probe_models(stubs4)),
          (rnd4.get("reviewers"), ar.probe_models(stubs4)))


# ---- T22-T23: UI lens roles, and a bad reviewer model that blocks only real work-start

UI_OFFERED = ["gpt-5.1", "gpt-5.2"]
NOT_OFFERED_WORDS = re.compile(r"not offer|does not offer|doesn't offer|isn't offered|not in (?:its|the) (?:offered |declared )?models",
                               re.I)
_UIP = {}


def ui_plan(d, env):
    """ui_review.py's own role_plan over the project's settings, in-process (the way tests/test_agents_reviewers.py's
    ui_order does for role_providers): (host, {lens: [runner, ...]}, {lens: chosen (provider, model) or None},
    {lens: [why a runner was set aside]}), or None when the call failed."""
    if "mod" not in _UIP:
        spec = importlib.util.spec_from_file_location("ratchet_ui_review_cp6", ar.UI_REVIEW)
        _UIP["mod"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_UIP["mod"])
    ns = type("Args", (), {"visual": None, "behavior": None})()
    with th.InProcessCall(cl.engine(), d, env, cl.RESET_GLOBALS, cl.LOCK_WAIT_CAP) as r:
        print(json.dumps(_UIP["mod"].role_plan(ns)))
    try:
        return tuple(json.loads(r.stdout)) if r.returncode == 0 else None
    except ValueError:
        return None


def _launch_arg(ran, lens, model):
    """True when the first runner that ran for `lens` was started with `--model <model>`."""
    argv = (ran.get(lens) or [("", "")])[0][1]
    return re.search(rf"--model {re.escape(model)}( |$)", argv) is not None


def t22_bad_ui_reviewer_model_is_refused_where_the_ui_review_launches():
    codex = {"id": "codex", "preset": "codex", "models": list(UI_OFFERED)}
    lists = {"visual": ["codex", "claude"], "behavior": ["codex", "claude"]}
    for bad_lens, good_lens in (("visual", "behavior"), ("behavior", "visual")):
        tag = f"{bad_lens} lens"
        proj = {"providers": [dict(codex)], "ui_reviewers": lists,
                "agents": {f"{bad_lens}-reviewer": "codex/gpt-9", f"{good_lens}-reviewer": "codex/gpt-5.2"}}
        d, env, stubs, m = ar.ui_world(proj)
        before = config_bytes(d, env)
        # 1. LOAD STAYS TOLERANT: config and models run; models shows the bad model as not offered, never as used
        for args in (("config",), ("models",)):
            r = cl.run(d, env, *args)
            check(f"[CP6-T22] ({tag}) `{args[0]}` still runs (exit 0) with the not-offered model in the file",
                  r.returncode == 0 and "Traceback" not in cl.text(r), (r.returncode, cl.text(r)[:300]))
        row, rm = ar.models_row(d, env, f"ui-reviewer-{bad_lens}")
        check(f"[CP6-T22] ({tag}) `models` shows gpt-9 as not offered by codex and never as the model in use",
              rm.returncode == 0 and "gpt-9" in ar.s(row) and "gpt-9" not in ar.s(row.get("effective"))
              and NOT_OFFERED_WORDS.search(ar.s(row)) is not None, row)
        okrow, _ = ar.models_row(d, env, f"ui-reviewer-{good_lens}")
        check(f"[CP6-T22] ({tag}) the other lens, set to a listed model, shows gpt-5.2 as in use and not unverified",
              "gpt-5.2" in ar.s(okrow.get("effective")) and "not verified" not in ar.s(okrow).lower(), okrow)
        check(f"[CP6-T22] ({tag}) loading and viewing changed nothing on disk", config_bytes(d, env) == before)
        # 2. the runner-picking entry (role_plan) drops codex for the bad lens only, and says why
        plan = ui_plan(d, env)
        host, order, _chosen, set_aside = plan if plan else (None, None, None, None)
        why = ar.s((set_aside or {}).get(bad_lens))
        check(f"[CP6-T22] ({tag}) the bad lens no longer tries codex: the next free runner (claude) comes next",
              order is not None and order.get(bad_lens) == ["claude"], (host, order))
        check(f"[CP6-T22] ({tag}) the other lens still tries codex first, then claude",
              order is not None and order.get(good_lens) == ["codex", "claude"], (host, order))
        check(f"[CP6-T22] ({tag}) the plan names gpt-9, codex and the offered models for the refusal",
              "gpt-9" in why and "codex" in why and all(x in why for x in UI_OFFERED), why)
        # 3. the real launch: the bad runner never starts, the next one does, the valid lens launches on gpt-5.2
        r, out = ar.run_ui_review(d, env, m)
        ran = ar.ui_ran(stubs)
        check(f"[CP6-T22] ({tag}) the UI review finishes (exit 0) and wrote a combined result",
              r.returncode == 0 and (out / "combined.json").exists(), (r.returncode, r.stderr[-300:]))
        check(f"[CP6-T22] ({tag}) the bad lens ran on claude, never on codex, and no runner was launched with gpt-9",
              [n for n, _ in ran.get(bad_lens, [])] == ["claude"]
              and not any("gpt-9" in a for rs in ran.values() for _n, a in rs), ran)
        check(f"[CP6-T22] ({tag}) the valid lens launched codex on gpt-5.2",
              [n for n, _ in ran.get(good_lens, [])] == ["codex"] and _launch_arg(ran, good_lens, "gpt-5.2"), ran)
        check(f"[CP6-T22] ({tag}) the run says why codex was refused, naming gpt-9, codex and the offered list",
              "gpt-9" in r.stderr and "codex" in r.stderr and all(x in r.stderr for x in UI_OFFERED), r.stderr[-400:])
        check(f"[CP6-T22] ({tag}) no paid approval was written",
              "paid_ok_at" not in ar.tree_text(d / ".ratchet", env["RATCHET_HOME"]))
        # 4. the repair command runs and the codex lens is back
        r = cl.run(d, env, "config", "--unset", f"agents.{bad_lens}-reviewer")
        check(f"[CP6-T22] ({tag}) `config --unset agents.{bad_lens}-reviewer` repairs it (exit 0)", r.returncode == 0,
              (r.returncode, cl.text(r)[:300]))
        plan2 = ui_plan(d, env)
        check(f"[CP6-T22] ({tag}) after the repair the lens tries codex first again",
              plan2 is not None and plan2[1].get(bad_lens) == ["codex", "claude"], plan2)
    # a misspelled bare model (no provider) for a UI lens: tolerant at load, shown as invalid, never launched
    proj = {"ui_reviewers": {"visual": ["claude", "codex"], "behavior": ["claude", "codex"]},
            "agents": {"visual-reviewer": "sonet"}}
    d, env, stubs, m = ar.ui_world(proj)
    for args in (("config",), ("models",)):
        r = cl.run(d, env, *args)
        check(f"[CP6-T22] (bare model) `{args[0]}` still runs (exit 0) with the misspelled sonet in the file",
              r.returncode == 0 and "Traceback" not in cl.text(r), (r.returncode, cl.text(r)[:300]))
    row, rm = ar.models_row(d, env, "ui-reviewer-visual")
    check("[CP6-T22] (bare model) `models` shows sonet as invalid or unknown and never as the model in use",
          "sonet" in ar.s(row) and "sonet" not in ar.s(row.get("effective"))
          and (INVALID_WORDS.search(ar.s(row)) is not None or NOT_OFFERED_WORDS.search(ar.s(row)) is not None), row)
    r, out = ar.run_ui_review(d, env, m)
    check("[CP6-T22] (bare model) the review still runs (exit 0) and no runner was launched with sonet",
          r.returncode == 0 and not any("sonet" in a for rs in ar.ui_ran(stubs).values() for _n, a in rs),
          (r.returncode, ar.ui_ran(stubs), r.stderr[-200:]))
    # a valid listed model on both lenses is applied and launched
    proj = {"providers": [dict(codex)], "ui_reviewers": lists,
            "agents": {"visual-reviewer": "codex/gpt-5.1", "behavior-reviewer": "codex/gpt-5.2"}}
    d, env, stubs, m = ar.ui_world(proj)
    r, out = ar.run_ui_review(d, env, m)
    ran = ar.ui_ran(stubs)
    check("[CP6-T22] (listed) each lens launched codex on its own listed model (gpt-5.1 and gpt-5.2)",
          r.returncode == 0 and _launch_arg(ran, "visual", "gpt-5.1") and _launch_arg(ran, "behavior", "gpt-5.2"),
          (r.returncode, ran))
    row, _ = ar.models_row(d, env, "ui-reviewer-visual")
    check("[CP6-T22] (listed) `models` shows the listed model as in use and does not call it unverified",
          "gpt-5.1" in ar.s(row.get("effective")) and "not verified" not in ar.s(row).lower(), row)
    check("[CP6-T22] (listed) no paid approval was written",
          "paid_ok_at" not in ar.tree_text(d / ".ratchet", env["RATCHET_HOME"]))
    # a provider that declares nothing: the model is passed through and `models` says it is not verified
    proj = {"providers": [dict(ar.CODEX)], "ui_reviewers": lists, "agents": {"visual-reviewer": "codex/gpt-5.9"}}
    d, env, stubs, m = ar.ui_world(proj)
    r, out = ar.run_ui_review(d, env, m)
    ran = ar.ui_ran(stubs)
    check("[CP6-T22] (declares nothing) the model is passed through to codex (--model gpt-5.9)",
          r.returncode == 0 and _launch_arg(ran, "visual", "gpt-5.9"), (r.returncode, ran))
    row, _ = ar.models_row(d, env, "ui-reviewer-visual")
    check("[CP6-T22] (declares nothing) `models` says that model is not verified, and never claims it verified",
          "not verified" in ar.s(row).lower() and re.search(r"(?<!not )verified", ar.s(row).lower()) is None, row)
    check("[CP6-T22] (declares nothing) no paid approval was written",
          "paid_ok_at" not in ar.tree_text(d / ".ratchet", env["RATCHET_HOME"]))


def docs_reviewable_world(providers, before_approval=None):
    """Like reviewable_world, but the step's only change since its base is README.md (docs only), so the outside review
    is skipped. The planned test checks README.md, the docs quick path is on (`doc_test_command`), and the adversarial
    step is the next one. `before_approval(d, env)` runs between `plan-submit` and approval, as in reviewable_world.
    Returns (dir, env, stubs)."""
    env = th.make_env("claude")
    stubs = ar.Stubs(env, ("codex", "gemini"))
    d = Path(_tmp.mkdtemp(prefix="ratchet-agents-launch-docs-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    (d / "README.md").write_text("# Title\n\noriginal sentence.\n")
    (d / "tests/cp1.test.js").write_text(
        "import t from 'node:test'; import a from 'node:assert'; import fs from 'node:fs';\n"
        "t('[CP1-T01] README names the install steps', () => a.ok(fs.readFileSync('README.md', 'utf8').includes('Install the thing')));\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    cl.project_path(d).write_text(json.dumps({"providers": [dict(p) for p in providers], "min_review_seconds": 0,
                                              "doc_test_command": "true {files}"}))
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
    cp = {"id": "CP1", "title": "Update the README", "summary": "s", "done_when": ["README names the install steps"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "normal",
          "tests": {"files": ["tests/cp1.test.js"], "command": "grep -q \"Install the thing\" README.md"}}
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    r = cl.run(d, env, "plan-submit")
    assert r.returncode == 0, cl.text(r)
    if before_approval:
        before_approval(d, env)
    cl.run(d, env, "ask", "approve-plan")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    hook("yes, go ahead")
    r = cl.run(d, env, "approve-plan")
    assert r.returncode == 0, cl.text(r)
    for a in (("tests-written", "CP1"), ("red", "CP1")):
        r = cl.run(d, env, *a)
        assert r.returncode == 0, cl.text(r)
    (d / "README.md").write_text("# Title\n\nInstall the thing\n\noriginal content, updated.\n")
    for a in (("implemented", "CP1"), ("behavior", "CP1")):
        r = cl.run(d, env, *a)
        assert r.returncode == 0, cl.text(r)
    return d, env, stubs


def approve_changes(d, env):
    """The legitimate way past the approval lock: the user asks for approve-changes, says yes, and it re-snapshots."""
    tp = Path(d) / "t.jsonl"
    cl.run(d, env, "ask", "approve-changes")
    tp.open("a").write(json.dumps({"type": "user", "message": {"role": "user", "content": "yes, go ahead"},
                                   "timestamp": th.now_dt().isoformat()}) + "\n")
    cl.run(d, env, "hook", "prompt", "--host", "claude", stdin=json.dumps(
        {"cwd": str(d), "prompt": "yes, go ahead", "session_id": "s-owner", "transcript_path": str(tp)}))
    return cl.run(d, env, "approve-changes")


def t23_bad_reviewer_model_blocks_only_real_work_start():
    offered = list(UI_OFFERED)
    prov = {"id": "codex", "preset": "codex", "models": list(offered)}
    bad = "codex/gpt-9"

    def put_bad(d_, e_):  # the setting is part of the approved config: written before the plan is approved
        write_proj(d_, dict(read_proj(d_), agents={"reviewer": bad}))

    # (a) a round for the step is already open: displaying it read-only never refuses and prints what it printed.
    # The round opened while the provider declared no models (gpt-9 passed through); the provider's `models` list is
    # filled in afterwards (not a locked rule), so the approved reviewer model is now one the provider does not offer.
    d, env, stubs, rnd = ar.approved_world({"providers": [dict(ar.CODEX), dict(ar.GEMINI)], "agents": {"reviewer": bad}})
    ids_before = [x["id"] for x in rnd.get("reviewers", [])]
    calls_before = stubs.calls()
    plain_before = cl.run(d, env, "reviewers", "CP1")
    cfg_a = read_proj(d)
    cfg_a["providers"] = [dict(prov) if p.get("id") == "codex" else p for p in cfg_a["providers"]]
    write_proj(d, cfg_a)
    row_a, _ = ar.models_row(d, env)
    check("[CP6-T23] (a, open round) setup: the approved model is now one the provider does not offer",
          "gpt-9" not in ar.s(row_a.get("effective")) and "gpt-9" in ar.s(row_a) and "not offer" in ar.s(row_a).lower(), row_a)
    plain_after = cl.run(d, env, "reviewers", "CP1")
    json_after = cl.run(d, env, "reviewers", "CP1", "--json")
    check("[CP6-T23] (a, open round) `reviewers CP1` read-only never refuses (exit 0)",
          plain_after.returncode == 0 and "Traceback" not in cl.text(plain_after), (plain_after.returncode, cl.text(plain_after)[:400]))
    check("[CP6-T23] (a, open round) `reviewers CP1 --json` read-only never refuses (exit 0)",
          json_after.returncode == 0 and "Traceback" not in cl.text(json_after), (json_after.returncode, cl.text(json_after)[:400]))
    try:
        shown = json.loads(json_after.stdout)
    except ValueError:
        shown = {}
    check("[CP6-T23] (a, open round) it prints the same open round as before (same reviewers and output folder)",
          [x["id"] for x in shown.get("reviewers", [])] == ids_before and shown.get("output_dir") == rnd.get("output_dir"),
          (shown.get("reviewers"), ids_before))
    check("[CP6-T23] (a, open round) the plain display prints exactly what it printed before the model was not offered",
          plain_after.stdout == plain_before.stdout, plain_after.stdout[-300:])
    check("[CP6-T23] (a, open round) nothing new was launched and the round is still open",
          stubs.calls() == calls_before and _round_open(d), stubs.calls())
    # (b) a docs-only step: the outside review is skipped, so there is nothing to refuse
    d2, env2, stubs2 = docs_reviewable_world([dict(prov), dict(ar.GEMINI)], before_approval=put_bad)
    r = cl.run(d2, env2, "reviewers", "CP1")
    out = cl.text(r)
    check("[CP6-T23] (b, docs-only) `reviewers CP1` never refuses (exit 0), whatever agents.reviewer says",
          r.returncode == 0 and "Traceback" not in out, (r.returncode, out[:400]))
    st = json.loads((d2 / ".ratchet/runs/f/state.json").read_text())
    gate = ((st.get("checkpoints") or {}).get("CP1", {}).get("gates") or {}).get("adversarial") or {}
    check("[CP6-T23] (b, docs-only) the outside review is recorded as skipped (gate passed) with no round and no stub CLI run",
          gate.get("status") == "passed" and not _round_open(d2) and stubs2.calls() == [], (gate, stubs2.calls()))
    check("[CP6-T23] (b, docs-only) it does not complain about gpt-9", "gpt-9" not in out, out[:400])
    # (c) a step where a round would open: refuses before any round opens or stub CLI runs
    d3, env3, stubs3 = reviewable_world([dict(prov), dict(ar.GEMINI)], before_approval=put_bad)
    where = str(cl.project_path(d3))
    r = cl.run(d3, env3, "reviewers", "CP1", "--json")
    out = cl.text(r)
    check("[CP6-T23] (c, round would open) `reviewers` refuses, naming the model, the provider and the file",
          r.returncode != 0 and "gpt-9" in out and "codex" in out and where in out and "Traceback" not in out,
          (r.returncode, out[:400]))
    check("[CP6-T23] (c, round would open) and names the models the provider offers", all(x in out for x in offered), out[:400])
    check("[CP6-T23] (c, round would open) no round was opened and no stub CLI ran",
          not _round_open(d3) and stubs3.calls() == [], stubs3.calls())
    # repair: removing the setting is a change to a locked rule, so it is confirmed the normal way (approve-changes)
    r = cl.run(d3, env3, "config", "--unset", "agents.reviewer")
    check("[CP6-T23] (c, round would open) `config --unset agents.reviewer` repairs it (exit 0)", r.returncode == 0,
          (r.returncode, cl.text(r)[:300]))
    r = approve_changes(d3, env3)
    check("[CP6-T23] (c, round would open) the removal is confirmed with approve-changes (exit 0)", r.returncode == 0,
          (r.returncode, cl.text(r)[:300]))
    r = cl.run(d3, env3, "reviewers", "CP1", "--json")
    check("[CP6-T23] (c, round would open) after removing the setting the same command works and opens the round",
          r.returncode == 0 and _round_open(d3), (r.returncode, cl.text(r)[:300]))
    # and a project that never had the setting opens the round straight away
    d4, env4, stubs4 = reviewable_world([dict(prov), dict(ar.GEMINI)])
    r = cl.run(d4, env4, "reviewers", "CP1", "--json")
    check("[CP6-T23] (c, round would open) a project without the setting opens the round",
          r.returncode == 0 and _round_open(d4), (r.returncode, cl.text(r)[:300]))


TESTS = [
    ("CP6-T01", t01_brief_names_the_chosen_model),
    ("CP6-T02", t02_no_choice_brief_unchanged),
    ("CP6-T03", t03_global_choice_reaches_the_brief_and_project_overrides),
    ("CP6-T04", t04_wrong_model_is_refused_with_the_right_name),
    ("CP6-T05", t05_launch_on_the_chosen_model_is_allowed),
    ("CP6-T06", t06_launch_without_a_model_is_refused),
    ("CP6-T07", t07_no_choice_every_launch_works),
    ("CP6-T08", t08_older_role_models_key_is_enforced_the_same_way),
    ("CP6-T09", t09_project_level_beats_global_at_launch),
    ("CP6-T10", t10_other_hosts_are_never_refused),
    ("CP6-T11", t11_non_ratchet_and_not_building_are_not_checked),
    ("CP6-T12", t12_a_choice_for_one_role_does_not_bind_another),
    ("CP6-T13", t13_unreadable_config_never_blocks_a_launch),
    ("CP6-T14", t14_view_says_a_host_that_cannot_pick_cannot_apply_the_choice),
    ("CP6-T15", t15_on_claude_code_the_view_says_the_choice_is_applied),
    ("CP6-T16", t16_readme_lists_the_new_file),
    ("CP6-T17", t17_inventory_registers_the_new_file),
    ("CP6-T18", t18_misspelled_sub_agent_model_is_refused_before_launch),
    ("CP6-T19", t19_not_offered_reviewer_model_is_refused_before_any_reviewer_launches),
    ("CP6-T20", t20_partway_failure_mark_names_the_model),
    ("CP6-T21", t21_hand_written_launch_model_changes_nothing),
    ("CP6-T22", t22_bad_ui_reviewer_model_is_refused_where_the_ui_review_launches),
    ("CP6-T23", t23_bad_reviewer_model_blocks_only_real_work_start),
]


# ---- X cases added after review: the older `role_models` key is judged and enforced exactly like `agents`

RM_IMPL = "ratchet-implementer"


def _rm_typo_world(level, value="sonet"):
    """A building run whose chosen model (through role_models, project or machine-wide) was valid at approval and is
    misspelled by hand afterwards. Returns (dir, env, file path)."""
    d, env = building({"role_models": {RM_IMPL: "opus"}})
    good = read_proj(d)
    if level == "project":
        write_proj(d, dict(good, role_models={RM_IMPL: value}))
        return d, env, str(cl.project_path(d))
    cl.put_global(env, {"role_models": {RM_IMPL: value}})
    write_proj(d, {k: v for k, v in good.items() if k != "role_models"})
    return d, env, str(cl.global_path(env))


def x01_misspelled_role_models_model_is_refused_like_agents():
    for level in ("project", "global"):
        d, env, where = _rm_typo_world(level)
        prompt = prompt_for_valid(d, env)
        for args in (("config",), ("status",), ("models",)):
            r = cl.run(d, env, *args)
            check(f"[CP6-X01] ({level}) `{args[0]}` still runs (exit 0) with the role_models typo present",
                  r.returncode == 0 and "Traceback" not in cl.text(r), (r.returncode, cl.text(r)[:300]))
        for label, model in (("the misspelled name", "sonet"), ("no model", NOMODEL), ("a valid alias", "opus")):
            r = guard(d, env, "ratchet-implementer", prompt, model)
            out = cl.text(r)
            check(f"[CP6-X01] ({level}) the guard refuses a launch naming {label} (exit 2) and names the file, "
                  f"role_models.{RM_IMPL}, sonet and the valid names",
                  r.returncode == 2 and where in out and f"role_models.{RM_IMPL}" in out and "sonet" in out
                  and all(n in out for n in VALID_ALIASES), (r.returncode, out[:400]))
        rb = brief(d, env, "implementer")
        out = cl.text(rb)
        check(f"[CP6-X01] ({level}) `brief` refuses, naming the file, role_models.{RM_IMPL}, sonet and the valid names",
              rb.returncode != 0 and where in out and f"role_models.{RM_IMPL}" in out and "sonet" in out
              and all(n in out for n in VALID_ALIASES), (rb.returncode, out[:400]))
        check(f"[CP6-X01] ({level}) `brief` printed no `Launch with model: sonet` line", f"{LAUNCH_LINE}sonet" not in rb.stdout)
        data, rm = models_json(d, env)
        row = detail_row(data, RM_IMPL)
        check(f"[CP6-X01] ({level}) `models --json` shows sonet as invalid and never as the real model",
              row.get("requested") == "sonet" and row.get("effective") != "sonet"
              and INVALID_WORDS.search(_row_text(row)) is not None, row)
        fix = cl.run(d, env, "config", *(("--global",) if level == "global" else ()), "--unset", f"role_models.{RM_IMPL}")
        check(f"[CP6-X01] ({level}) the repair named in the message (`config --unset role_models.{RM_IMPL}`) works (exit 0)",
              fix.returncode == 0, cl.text(fix)[:300])
        check(f"[CP6-X01] ({level}) after the repair `brief` runs again", brief(d, env, "implementer").returncode == 0)
    # a tier and a valid alias written through role_models are valid names, not typos
    for value in ("light", "strong", "alternate", "inherit", "opus", "claude-opus-4-1"):
        d, env = building({"role_models": {RM_IMPL: value}})
        r = brief(d, env, "implementer")
        check(f"[CP6-X01] role_models {value!r} is not refused by `brief`", r.returncode == 0, cl.text(r)[:300])
    # `config --set` refuses to WRITE the typo through the older key too, and leaves the files as they were
    for flags, label in (((), "project"), (("--global",), "global")):
        d, env = cl.project({"related_tests_max": 4})
        before = config_bytes(d, env)
        r = cl.run(d, env, "config", *flags, "--set", f"role_models.{RM_IMPL}=sonet")
        out = cl.text(r)
        check(f"[CP6-X01] ({label}) `config --set role_models.{RM_IMPL}=sonet` refuses to write it, naming sonet and the valid names",
              r.returncode != 0 and "sonet" in out and all(n in out for n in VALID_ALIASES) and "Traceback" not in out,
              (r.returncode, out[:300]))
        check(f"[CP6-X01] ({label}) ... and nothing was written", config_bytes(d, env) == before)
        ok = cl.run(d, env, "config", *flags, "--set", f"role_models.{RM_IMPL}=light")
        check(f"[CP6-X01] ({label}) a tier is written (exit 0)", ok.returncode == 0, cl.text(ok)[:300])


def prompt_for_valid(d, env):
    """A launch prompt carrying the plan's objective for a run whose chosen model is misspelled (so `brief` refuses): the
    prompt is built from a throwaway valid-settings copy of the same run's objective text."""
    d2, env2 = building()
    r = brief(d2, env2, "implementer")
    assert r.returncode == 0, cl.text(r)
    return r.stdout + "\n\nGo do the work for checkpoint CP1 now."


def x02_valid_role_models_value_is_enforced_at_the_guard():
    d, env = building({"role_models": {RM_IMPL: "light"}})   # a tier: the claude host's light model is haiku
    prompt = prompt_for(d, env)
    check("[CP6-X02] `brief` prints the light tier's model as the launch model",
          f"{LAUNCH_LINE}haiku" in brief(d, env).stdout, brief(d, env).stdout[:300])
    for label, model, want in (("opus", "opus", 2), ("no model", NOMODEL, 2), ("haiku", "haiku", 0)):
        r = guard(d, env, "ratchet-implementer", prompt, model)
        out = cl.text(r)
        check(f"[CP6-X02] role_models light: a launch on {label} exits {want}", r.returncode == want, out[-300:])
        if want == 2:
            check(f"[CP6-X02] the refusal names haiku and role_models.{RM_IMPL}", "haiku" in out and f"role_models.{RM_IMPL}" in out,
                  out[-300:])


def x03_role_models_and_agents_the_winner_is_judged():
    # project role_models beats global agents, whichever key the lower file used
    d, env = building({"role_models": {RM_IMPL: "haiku"}}, glob={"agents": {"implementer": "opus"}})
    prompt = prompt_for(d, env)
    r = guard(d, env, "ratchet-implementer", prompt, "opus")
    check("[CP6-X03] project role_models haiku beats global agents opus: a launch on opus is refused naming haiku",
          r.returncode == 2 and "haiku" in cl.text(r), cl.text(r)[-300:])
    check("[CP6-X03] ... and a launch on haiku exits 0", guard(d, env, "ratchet-implementer", prompt, "haiku").returncode == 0)
    # project agents beats global role_models: a typo in the shadowed global role_models value decides nothing
    d, env = building({"agents": {"implementer": "opus"}})
    cl.put_global(env, {"role_models": {RM_IMPL: "sonet"}})
    prompt = prompt_for(d, env)
    check("[CP6-X03] a shadowed global role_models typo does not refuse `brief`", brief(d, env).returncode == 0)
    check("[CP6-X03] ... the launch on the project's agents value (opus) exits 0",
          guard(d, env, "ratchet-implementer", prompt, "opus").returncode == 0)
    # project role_models beats a global typo (agents or role_models)
    for glob in ({"agents": {"implementer": "sonet"}}, {"role_models": {RM_IMPL: "sonet"}}):
        d, env = building({"role_models": {RM_IMPL: "opus"}}, glob=glob)
        prompt = prompt_for(d, env)
        check(f"[CP6-X03] project role_models opus beats the global typo {list(glob)[0]}: `brief` runs",
              brief(d, env).returncode == 0)
        check("[CP6-X03] ... and a launch on opus exits 0", guard(d, env, "ratchet-implementer", prompt, "opus").returncode == 0)
    # global agents beats nothing above it but a project role_models typo: the project's wins and is judged
    d, env = building(glob={"agents": {"implementer": "opus"}})
    prompt = prompt_for(d, env)
    write_proj(d, dict(read_proj(d), role_models={RM_IMPL: "sonet"}))
    rb = brief(d, env)
    check("[CP6-X03] a project role_models typo over a valid global agents value refuses `brief`, naming the project file",
          rb.returncode != 0 and str(cl.project_path(d)) in cl.text(rb) and f"role_models.{RM_IMPL}" in cl.text(rb),
          cl.text(rb)[:400])
    # an agents choice this host can't apply (another company's provider/model) falls back to the role_models value
    # below it, which is then what runs: it is enforced, and a typo there is refused
    d, env = building({"agents": {"implementer": "codex/gpt-5"}}, glob={"role_models": {RM_IMPL: "haiku"}})
    prompt = prompt_for(d, env)
    r = guard(d, env, "ratchet-implementer", prompt, "opus")
    check("[CP6-X03] agents that can't apply here over a global role_models haiku: a launch on opus is refused naming haiku "
          f"and role_models.{RM_IMPL}",
          r.returncode == 2 and "haiku" in cl.text(r) and f"role_models.{RM_IMPL}" in cl.text(r), cl.text(r)[-300:])
    check("[CP6-X03] ... a launch on haiku exits 0", guard(d, env, "ratchet-implementer", prompt, "haiku").returncode == 0)
    check("[CP6-X03] ... and `brief` prints Launch with model: haiku", f"{LAUNCH_LINE}haiku" in brief(d, env).stdout)
    cl.put_global(env, {"role_models": {RM_IMPL: "sonet"}})
    rb = brief(d, env)
    check("[CP6-X03] the same fallback holding a typo refuses `brief`, naming the global file",
          rb.returncode != 0 and str(cl.global_path(env)) in cl.text(rb) and "sonet" in cl.text(rb), cl.text(rb)[:400])
    # with only the shipped default behind an inapplicable agents choice nothing is enforced
    d, env = building({"agents": {"implementer": "codex/gpt-5"}})
    prompt = prompt_for(d, env)
    check("[CP6-X03] agents that can't apply and no role_models value: every launch works as today",
          all(guard(d, env, "ratchet-implementer", prompt, m).returncode == 0 for m in ("opus", "haiku", NOMODEL)))


def x04_other_hosts_and_unreadable_config_still_never_block_a_role_models_typo():
    d, env, where = _rm_typo_world("project")
    prompt = prompt_for_valid(d, env)
    r = guard(d, env, "ratchet-implementer", prompt, "sonet", host="codex", tool="Task", extra={"turn_id": "t-1"})
    check("[CP6-X04] --host codex never refuses a role_models typo (exit 0)", r.returncode == 0, cl.text(r)[-300:])
    r = guard(d, env, "general-purpose", "Explore the repository.", "sonet")
    check("[CP6-X04] a non-Ratchet sub-agent is never refused for it (exit 0)", r.returncode == 0, cl.text(r)[-300:])
    r = guard(d, env, "ratchet-fixer", prompt, "opus")
    check("[CP6-X04] a typo for the implementer does not refuse a fixer launch (exit 0)", r.returncode == 0, cl.text(r)[-300:])
    cl.project_path(d).write_text("{not valid json at all")
    r = guard(d, env, "ratchet-implementer", prompt, "opus")
    check("[CP6-X04] a damaged config still fails the guard open (exit 0) and records the crash",
          r.returncode == 0 and crash_recorded(d), cl.text(r)[-300:])


TESTS.extend([
    ("CP6-X01", x01_misspelled_role_models_model_is_refused_like_agents),
    ("CP6-X02", x02_valid_role_models_value_is_enforced_at_the_guard),
    ("CP6-X03", x03_role_models_and_agents_the_winner_is_judged),
    ("CP6-X04", x04_other_hosts_and_unreadable_config_still_never_block_a_role_models_typo),
])


def x05_a_missing_program_marks_every_reviewer_behind_it_with_no_model():
    glm = {"id": "opencode-glm", "preset": "opencode", "model": "opencode-go/glm-5.3", "cost": "flat"}
    kimi = {"id": "opencode-kimi", "preset": "opencode", "model": "opencode-go/kimi-k2.6", "cost": "flat"}
    d, env, stubs, data = ar.approved_world(lambda st: {"providers": [dict(glm), dict(kimi)],
                                                        "agents": {"reviewer": "opencode-kimi/model-x"}},
                                            stub_names=("codex", "gemini", "opencode"))
    row = [(x["id"], x.get("model")) for x in data.get("reviewers", [])]
    check("[CP6-X05] (setup) the round starts with opencode-kimi-both on the chosen model-x",
          row == [("opencode-kimi-both", "model-x")], row)
    hidden = stubs.bin / "opencode.away"
    (stubs.bin / "opencode").rename(hidden)
    r = run_review(d, env, "opencode-kimi-both", Path(data["output_dir"]) / "opencode-kimi-both.json")
    hidden.rename(stubs.bin / "opencode")
    check("[CP6-X05] the review fails because the program is missing (exit 3, UNAVAILABLE)",
          r.returncode == 3 and "UNAVAILABLE" in r.stderr, (r.returncode, r.stderr[-300:]))
    marks = json.loads(ar.unavailable_path(d).read_text()) if ar.unavailable_path(d).exists() else {}
    both = [k for k in marks if k.startswith("opencode-")]
    check("[CP6-X05] every reviewer behind the program is marked, the glm ones included",
          {"opencode-glm-both", "opencode-kimi-both"} <= set(both), marks)
    check("[CP6-X05] none of those marks names a model (a missing program is about every model behind it)",
          all("model" not in marks[k] for k in both), marks)
    base = read_proj(d)

    def behind_program_out(tag):
        res = ar.resolve(d, env, "low")
        check(f"[CP6-X05] ({tag}) no opencode reviewer is given the slot", res.out is not None
              and not [i for i in res.ids if i.startswith("opencode-")], (res.ids, res.err[-200:]))
    behind_program_out("choice still model-x")
    write_proj(d, dict(base, agents={"reviewer": "opencode-kimi/model-y"}))
    behind_program_out("choice changed to model-y")
    gone = dict(base)
    gone.pop("agents", None)
    write_proj(d, gone)
    behind_program_out("choice removed")
    row, _ = ar.models_row(d, env)
    check("[CP6-X05] and the models view does not call opencode the reviewer in use",
          "opencode" not in ar.s(row.get("effective")).lower(), row)
    write_proj(d, base)


TESTS.extend([
    ("CP6-X05", x05_a_missing_program_marks_every_reviewer_behind_it_with_no_model),
])


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
