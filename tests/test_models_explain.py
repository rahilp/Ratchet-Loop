#!/usr/bin/env python3
"""
CP4 tests: see requested versus real model per role.   python3 tests/test_models_explain.py

`models` (and `models --json`) is the one read-only view of every Ratchet role: the sub-agent roles in the roles
table plus orchestrator, director, reviewer and the UI reviewers. Each gets what was asked for (requested), what will
really be used (effective), where the request came from (source: builtin, global or project) and, when the two
differ, a plain reason (an unknown tier on this host, a host that cannot choose a model per sub-agent, a provider
that is unavailable or out of quota, the same company as the host, a paid model with no approval). The old JSON
fields (host, roles, alternates) and the old text rows stay exactly as they were; the new per-role list is an extra
"detail" key. The command never contacts a provider and never spends anything.

Everything is driven through the engine's own CLI entry point, in-process (standard 30), each case in its own
throwaway git project with its own private per-user Ratchet home (helpers from tests/test_config_layers.py and
tests/test_agents_config.py). Fake agent CLIs on a private PATH write a marker file if they are ever run, so
"nothing was contacted" is provable. No network, no real reviewer CLI.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP4-T01",
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
import test_config_layers as cl                         # noqa: E402  (in-process helpers: project(), run(), ...)
import test_agents_config as ac                         # noqa: E402  (approved(), to_review(), jload())

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP4-[TX]\d+)\]")


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
jload = ac.jload

# What `models --json` printed before this change, with no setting anywhere, on an anthropic host.
OLD_ROLES = {
    "ratchet-adversary": "inherit", "ratchet-app-scout": "sonnet", "ratchet-behavior-reviewer": "sonnet",
    "ratchet-checkpoint-planner": "inherit", "ratchet-design-system": "sonnet", "ratchet-fixer": "inherit",
    "ratchet-implementer": "inherit", "ratchet-prototyper": "sonnet", "ratchet-test-planner": "sonnet",
    "ratchet-test-runner": "haiku", "ratchet-test-writer": "sonnet", "ratchet-variant-writer": "sonnet",
    "ratchet-visual-reviewer": "inherit",
}
OLD_HEADER = "Host: anthropic. Spawn each role's sub-agent with this model (inherit = your own model):"
OLD_PLAN_MODELS_LINE = (
    "Models: your session's model for this agent's own code reviews, planning the steps, fixing review findings, "
    "writing the code, visual UI review; sonnet for checking the app runs, UI behaviour review, the design file, the "
    "prototype, planning tests, writing tests, disguised examples for high-risk checks; haiku for running tests")
SUB_ROLES = sorted(r[len("ratchet-"):] for r in OLD_ROLES)
HOST_ROLES = ("orchestrator", "director", "reviewer")
FAR = "2099-01-01T00:00:00+00:00"
LONG_AGO = "2000-01-01T00:00:00+00:00"
AGENT_CLIS = ("codex", "claude", "gemini", "copilot", "opencode", "cursor-agent", "agent", "omp")


def fake_clis(env, names=AGENT_CLIS, exit_code=1):
    """Put fake agent CLIs first on this env's PATH. Each appends one line to a log if it is ever run. Returns the log
    path (it exists only once some fake ran)."""
    bindir = Path(_tmp.mkdtemp(prefix="ratchet-fakeclis-"))
    log = bindir / "ran.log"
    for n in names:
        p = bindir / n
        p.write_text(f'#!/bin/sh\necho "{n} $@" >> "{log}"\nexit {exit_code}\n')
        p.chmod(0o755)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return log


def detail(data) -> dict:
    """{plain role name (no 'ratchet-' prefix, '_' as '-'): entry} from the new per-role list; {} when absent."""
    out = {}
    lst = data.get("detail") if isinstance(data, dict) else None
    for e in lst if isinstance(lst, list) else []:
        if isinstance(e, dict) and isinstance(e.get("role"), str):
            out[e["role"].replace("ratchet-", "", 1).replace("_", "-")] = e
    return out


def view(d, env, *extra):
    """(parsed `models --json` or None, detail by role, the call)"""
    r = run(d, env, "models", "--json", *extra)
    data = jload(r)
    return data, detail(data), r


def ui_entries(det) -> list:
    return [e for k, e in det.items() if k.startswith("ui-reviewer")]


def s(v) -> str:
    return json.dumps(v) if not isinstance(v, str) else v


def sessionish(v) -> bool:
    return v in (None, "inherit") or "session" in s(v).lower()


def file_hash(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def write_unavailable(d, rids):
    (Path(d) / ".ratchet" / "unavailable-reviewers.json").write_text(json.dumps(
        {rid: {"reason": "timeout", "since": LONG_AGO, "until": FAR} for rid in rids}))


def write_bench(env, key, quota=True):
    p = Path(env["RATCHET_HOME"]) / "provider-health.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({key: {"bench": {"reason": "out of quota", "since": LONG_AGO, "until": FAR,
                                             "quota": quota}}}))


CODEX = {"id": "codex", "preset": "codex", "model": "gpt-5"}


# ------------------------------------------------------------------------------------------------ cases

def t01_every_role_listed():
    d, env = project({})
    data, det, r = view(d, env)
    check("[CP4-T01] models --json succeeds and prints a 'detail' list", r.returncode == 0 and isinstance(
        (data or {}).get("detail"), list), text(r)[:300])
    missing = [x for x in list(SUB_ROLES) + list(HOST_ROLES) if x not in det]
    check("[CP4-T01] every sub-agent role, orchestrator, director and reviewer has an entry", not missing, missing)
    check("[CP4-T01] a ui-reviewer entry is listed", len(ui_entries(det)) >= 1, sorted(det))
    keys_ok = all({"requested", "effective", "source", "reason"} <= set(e) for e in det.values()) and bool(det)
    check("[CP4-T01] each entry has requested, effective, source and reason", keys_ok,
          [(k, sorted(e)) for k, e in det.items() if not {"requested", "effective", "source", "reason"} <= set(e)][:3])
    check("[CP4-T01] source is builtin for every role when nothing is set",
          bool(det) and all(e.get("source") == "builtin" for e in det.values()),
          {k: e.get("source") for k, e in det.items()})


def t02_old_fields_unchanged():
    d, env = project({})
    data, _, r = view(d, env)
    check("[CP4-T02] anthropic host: host, roles and alternates keep today's values",
          isinstance(data, dict) and data.get("host") == "anthropic" and data.get("roles") == OLD_ROLES
          and data.get("alternates") == ["sonnet", "opus"], text(r)[:300])
    check("[CP4-T02] the only new top-level key is 'detail'",
          isinstance(data, dict) and set(data) == {"host", "roles", "alternates", "detail"}, sorted(data or {}))
    env2 = dict(env, RATCHET_HOST_PROVIDER="github")
    data2, _, r2 = view(d, env2)
    check("[CP4-T02] github host: every role inherit, no alternates, host github",
          isinstance(data2, dict) and data2.get("host") == "github"
          and data2.get("roles") == {k: "inherit" for k in OLD_ROLES} and data2.get("alternates") == []
          and "detail" in data2, text(r2)[:300])


def t03_text_keeps_old_lines_adds_new():
    d, env = project({})
    r = run(d, env, "models")
    lines = r.stdout.splitlines()
    old_rows = [f"  {role:<28} {m}" for role, m in sorted(OLD_ROLES.items())]
    check("[CP4-T03] the header line and the thirteen old rows keep their layout, in order",
          r.returncode == 0 and lines[:1] == [OLD_HEADER] and lines[1:14] == old_rows, r.stdout[:400])
    extra = [ln for ln in lines[14:] if ln.strip()]
    for role in HOST_ROLES:
        row = next((ln for ln in extra if role in ln.lower()), "")
        check(f"[CP4-T03] a row after the old ones names {role} with its source", bool(row) and "builtin" in row.lower(),
              extra)


def t04_project_source():
    d, env = project({"agents": {"implementer": "sonnet"}}, glob={})
    data, det, r = view(d, env)
    e = det.get("implementer") or {}
    check("[CP4-T04] implementer: requested sonnet, effective sonnet, source project, no reason",
          e.get("requested") == "sonnet" and e.get("effective") == "sonnet" and e.get("source") == "project"
          and not e.get("reason"), (e, text(r)[:200]))
    check("[CP4-T04] a role nobody set stays builtin", (det.get("fixer") or {}).get("source") == "builtin", det.get("fixer"))


def t05_global_and_project():
    d, env = project({"agents": {"implementer": "sonnet"}},
                     glob={"agents": {"implementer": "opus", "test-writer": "haiku"}})
    data, det, r = view(d, env)
    i, w = det.get("implementer") or {}, det.get("test-writer") or {}
    check("[CP4-T05] implementer shows sonnet from the project (it wins over the global opus)",
          i.get("requested") == "sonnet" and i.get("effective") == "sonnet" and i.get("source") == "project", (i, text(r)[:200]))
    check("[CP4-T05] test-writer shows haiku from the global file",
          w.get("requested") == "haiku" and w.get("effective") == "haiku" and w.get("source") == "global", w)


def t06_unknown_tier_explained():
    d, env = project({"agents": {"implementer": "turbo"}})
    data, det, r = view(d, env)
    e = det.get("implementer") or {}
    why = s(e.get("reason")).lower()
    check("[CP4-T06] requested shows the tier, effective is the session's own model",
          e.get("requested") == "turbo" and sessionish(e.get("effective")) and e.get("effective") != "turbo", e)
    check("[CP4-T06] the reason says the tier is unknown on this host",
          "unknown" in why and ("host" in why or "anthropic" in why), e)
    check("[CP4-T06] the command did not crash", r.returncode == 0 and "Traceback" not in text(r), text(r)[:300])


def t07_host_cannot_choose():
    for host in ("github", "openai"):
        d, env = project({"agents": {"implementer": "sonnet"}})
        env["RATCHET_HOST_PROVIDER"] = host
        data, det, r = view(d, env)
        e = det.get("implementer") or {}
        why = s(e.get("reason")).lower()
        check(f"[CP4-T07] {host}: requested stays sonnet, effective is the session's own model",
              e.get("requested") == "sonnet" and sessionish(e.get("effective")) and e.get("effective") != "sonnet", e)
        check(f"[CP4-T07] {host}: the reason says the host cannot choose a model per sub-agent",
              ("choose" in why or "pick" in why or "select" in why) and "sub-agent" in why, e)
        check(f"[CP4-T07] {host}: the old roles map shows nothing faked as applied",
              (data or {}).get("roles", {}).get("ratchet-implementer") == "inherit", text(r)[:200])


def t08_missing_host_entry():
    hm = {"anthropic": {"strong": "inherit", "standard": "sonnet", "alternates": ["sonnet", "opus"]}}
    d, env = project({"host_models": hm})
    data, det, r = view(d, env)
    e = det.get("test-runner") or {}
    why = s(e.get("reason")).lower()
    check("[CP4-T08] the command succeeds and does not crash", r.returncode == 0 and "Traceback" not in text(r), text(r)[:300])
    check("[CP4-T08] test-runner (asks for the light tier) falls back to the session's model",
          sessionish(e.get("effective")) and (data or {}).get("roles", {}).get("ratchet-test-runner") == "inherit", e)
    check("[CP4-T08] the reason names the missing host entry (the light tier)",
          "light" in why and ("host" in why or "no entry" in why or "missing" in why), e)


def t09_reviewer_who_really_reviews():
    d, env = project({"providers": [CODEX], "agents": {"reviewer": "codex/gpt-5"}})
    fake_clis(env)
    data, det, r = view(d, env)
    e = det.get("reviewer") or {}
    check("[CP4-T09] requested names codex and gpt-5",
          "codex" in s(e.get("requested")).lower() and "gpt-5" in s(e.get("requested")), e)
    check("[CP4-T09] effective names the plain reviewer (Codex, gpt-5)", "Codex, gpt-5" in s(e.get("effective")), e)
    check("[CP4-T09] no reason is shown and the source is project", not e.get("reason") and e.get("source") == "project", e)


def t10_unavailable_and_quota():
    d, env = project({"providers": [CODEX], "agents": {"reviewer": "codex/gpt-5"}})
    fake_clis(env)
    write_unavailable(d, ["codex-both", "codex-standards", "codex-correctness"])
    data, det, r = view(d, env)
    e = det.get("reviewer") or {}
    why = s(e.get("reason")).lower()
    check("[CP4-T10] unavailable: effective is the fallback, not Codex",
          r.returncode == 0 and e.get("effective") and "codex" not in s(e.get("effective")).lower(), e)
    check("[CP4-T10] unavailable: the reason says the provider is unavailable",
          "unavailable" in why or "failed recently" in why, e)
    d2, env2 = project({"providers": [CODEX], "agents": {"reviewer": "codex/gpt-5"}})
    fake_clis(env2)
    write_bench(env2, "codex:gpt-5")
    data2, det2, r2 = view(d2, env2)
    e2 = det2.get("reviewer") or {}
    check("[CP4-T10] out of quota: effective is the fallback, not Codex",
          r2.returncode == 0 and e2.get("effective") and "codex" not in s(e2.get("effective")).lower(), e2)
    check("[CP4-T10] out of quota: the reason says quota", "quota" in s(e2.get("reason")).lower(), e2)


def t11_same_company_as_host():
    d, env = project({"providers": [{"id": "claude", "preset": "claude"}], "agents": {"reviewer": "claude/opus"}})
    fake_clis(env)
    data, det, r = view(d, env)
    e = det.get("reviewer") or {}
    check("[CP4-T11] the reason says it is the same company as the host",
          "same company" in s(e.get("reason")).lower(), e)
    check("[CP4-T11] effective is the host's own reviewer",
          "this agent" in s(e.get("effective")).lower() and "claude" not in s(e.get("effective")).lower(), e)


def t12_paid_not_approved():
    paid = {"id": "paidy", "preset": "codex", "cost": "per-use", "model": "gpt-5"}
    for where in ("project", "global"):
        if where == "project":
            d, env = project({"providers": [paid], "agents": {"reviewer": "paidy/gpt-5"}})
        else:
            d, env = project({"providers": [paid]}, glob={"agents": {"reviewer": "paidy/gpt-5"}})
        log = fake_clis(env)
        data, det, r = view(d, env)
        e = det.get("reviewer") or {}
        why = s(e.get("reason")).lower()
        check(f"[CP4-T12] {where}: the reason says it needs paid approval",
              "paid" in why or "per use" in why or "per-use" in why or "approv" in why, e)
        check(f"[CP4-T12] {where}: effective is not the paid provider",
              e.get("effective") and "paidy" not in s(e.get("effective")).lower()
              and "gpt-5" not in s(e.get("effective")), e)
        blob = ""
        for base in (Path(d) / ".ratchet", Path(env["RATCHET_HOME"])):
            for p in sorted(base.rglob("*")) if base.exists() else []:
                if p.is_file():
                    blob += p.read_text(errors="ignore")
        check(f"[CP4-T12] {where}: paid_ok_at exists nowhere afterwards and nothing was run",
              "paid_ok_at" not in blob and not log.exists(), (log.exists(), blob.count("paid_ok_at")))


def t13_ui_reviewers():
    d, env = project({})
    data, det, r = view(d, env)
    ui = ui_entries(det)
    check("[CP4-T13] default config lists ui-reviewer entries with source builtin",
          len(ui) >= 1 and all(e.get("source") == "builtin" for e in ui), det)
    d2, env2 = project({"providers": [{"id": "codex", "preset": "codex"}],
                        "ui_reviewers": {"visual": ["codex"], "behavior": ["codex"]}})
    fake_clis(env2)
    data2, det2, r2 = view(d2, env2)
    ui2 = ui_entries(det2)
    check("[CP4-T13] with ui_reviewers set, the entries show source project and the codex provider",
          len(ui2) >= 1 and all(e.get("source") == "project" and "codex" in (s(e.get("requested")) + s(e.get("effective"))).lower()
                                for e in ui2), ui2)


def t14_orchestrator_director_never_switched():
    d, env = project({"agents": {"orchestrator": "opus", "director": "sonnet"}})
    data, det, r = view(d, env)
    o, di = det.get("orchestrator") or {}, det.get("director") or {}
    check("[CP4-T14] orchestrator shows requested opus from the project, director sonnet from the project",
          o.get("requested") == "opus" and o.get("source") == "project"
          and di.get("requested") == "sonnet" and di.get("source") == "project", (o, di))
    check("[CP4-T14] neither is claimed as applied: effective is the running session's model, with a reason",
          sessionish(o.get("effective")) and sessionish(di.get("effective")) and o.get("reason") and di.get("reason"),
          (o, di))
    r2 = run(d, env, "models")
    out = (text(r2) + json.dumps(data)).lower()
    check("[CP4-T14] no line claims a running session was switched",
          not re.search(r"(has been|was|now|is) switched|switched to|now running (on )?(opus|sonnet)", out), out[-400:])
    check("[CP4-T14] the plain text names both requests", "opus" in r2.stdout and "sonnet" in r2.stdout
          and "orchestrator" in r2.stdout and "director" in r2.stdout, r2.stdout[-400:])


def t15_contacts_nothing_spends_nothing():
    d, env = project({"providers": [CODEX, {"id": "paidy", "preset": "codex", "cost": "per-use", "model": "gpt-5.5"}],
                      "agents": {"reviewer": "codex/gpt-5"}})
    log = fake_clis(env)
    write_unavailable(d, ["gemini-both"])
    write_bench(env, "codex:gpt-5")
    (Path(d) / ".ratchet" / "provider-status.json").write_text(json.dumps({"codex": {"ok": True}}))
    files = [Path(d) / ".ratchet" / "unavailable-reviewers.json", Path(d) / ".ratchet" / "provider-status.json",
             Path(env["RATCHET_HOME"]) / "provider-health.json", project_path(d), cl.global_path(env)]
    before = [file_hash(p) for p in files]
    codes = [run(d, env, "models").returncode, run(d, env, "models", "--json").returncode]
    check("[CP4-T15] both forms exit 0", codes == [0, 0], codes)
    check("[CP4-T15] no fake agent CLI ran", not log.exists(), log.read_text() if log.exists() else "")
    check("[CP4-T15] the quota, health, status, unavailable and config files are byte-for-byte unchanged",
          [file_hash(p) for p in files] == before, [p.name for p, b in zip(files, before) if file_hash(p) != b])
    data = jload(run(d, env, "models", "--json"))
    check("[CP4-T15] the view really ran (the detail list is there)", isinstance(data, dict) and "detail" in data, data)


def t16_bad_names_refused():
    cases = (("unknown role", {"agents": {"nobody": "opus"}}, "nobody"),
             ("unknown provider", {"agents": {"implementer": "nosuchprov/gpt-5"}}, "nosuchprov"))
    for label, cfg, name in cases:
        d, env = project(cfg)
        for args in (("models",), ("models", "--json")):
            r = run(d, env, *args)
            out = text(r)
            check(f"[CP4-T16] {label}, `{' '.join(args)}`: exits non-zero naming the bad name and the file",
                  r.returncode != 0 and name in out and "config.json" in out and "Traceback" not in out, (r.returncode, out[:300]))
            check(f"[CP4-T16] {label}, `{' '.join(args)}`: no table or JSON is printed",
                  not r.stdout.strip(), r.stdout[:200])


def t17_damaged_global_file():
    d, env = project({})
    put_global(env, raw="{ not json")
    r = run(d, env, "models", "--json")
    out = text(r)
    check("[CP4-T17] exits non-zero naming the global file path, no traceback",
          r.returncode != 0 and str(global_path(env)) in out and "Traceback" not in out, (r.returncode, out[:300]))
    check("[CP4-T17] stdout holds no half-printed JSON", not r.stdout.strip(), r.stdout[:200])


def t18_defaults_match_today():
    d, env = project({}, glob=None)
    data, det, r = view(d, env)
    check("[CP4-T18] the roles map equals today's, role by role", (data or {}).get("roles") == OLD_ROLES, text(r)[:300])
    check("[CP4-T18] the detail list is present too", bool(det), data)
    d2, env2 = ac.approved()
    r2 = run(d2, env2, "plan-summary")
    line = next((ln for ln in r2.stdout.splitlines() if ln.startswith("Models:")), "")
    check("[CP4-T18] the plan-summary Models line keeps its wording", line == OLD_PLAN_MODELS_LINE, line)


def t19_listing_changes_nobody():
    cfg = {"providers": [CODEX], "agents": {"implementer": "sonnet", "test-writer": "haiku", "reviewer": "codex/gpt-5",
                                              "orchestrator": "opus", "director": "sonnet"}}
    ids = []
    for listed in (False, True):
        d, env = ac.approved(glob_before={"agents": cfg["agents"]}, providers=cfg["providers"])
        fake_clis(env, exit_code=0)
        if listed:
            for args in (("models",), ("models", "--json"), ("models",)):
                run(d, env, *args)
        got, _ = ac.to_review(d, env)
        ids.append(got)
    check("[CP4-T19] the same reviewer ids, in the same count, with and without listing models first",
          ids[0] == ids[1] and len(ids[0]) >= 1, ids)


def t20_readme_lists_file():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("### Running the tests", 1)[-1]
    n = len(re.findall(r"^python3 tests/test_models_explain\.py$", section, re.M))
    check("[CP4-T20] the README 'Running the tests' block lists python3 tests/test_models_explain.py exactly once",
          n == 1, n)


def t21_inventory_matches_what_the_file_reads():
    src = Path(__file__).read_text()
    # Patterns assembled from pieces so this file's own source never matches them by accident.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(src) or src_pat.search(src))
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    listed = "test_models_explain.py" in inv
    check("[CP4-T21] the file is in the test inventory exactly when it reads engine source or loads it by spec",
          scans == listed, f"scans={scans} listed={listed}")


# ---- extra cases: the director's rulings that the plan's cases do not pin on their own

def x01_advisory_on_stderr_not_stdout():
    d, env = project({"agents": {"implementer": "turbo"}})
    base_d, base_env = project({})
    r = run(d, env, "models", "--json")
    data = jload(r)
    check("[CP4-X01] a typo'd bare model is flagged on stderr naming the key and the value, without failing",
          r.returncode == 0 and "agents.implementer" in r.stderr and "turbo" in r.stderr, (r.returncode, r.stderr[:300]))
    check("[CP4-X01] stdout stays clean JSON with the new detail list",
          isinstance(data, dict) and "detail" in data and "turbo" not in json.dumps(data.get("roles")), r.stdout[:200])
    r0 = run(base_d, base_env, "models", "--json")
    check("[CP4-X01] with nothing set, stderr is empty", r0.returncode == 0 and not r0.stderr.strip(), r0.stderr[:200])


def x02_wrong_type_value_flagged_not_fatal():
    d, env = project({"agents": {"implementer": "sonnet"}})
    p = project_path(d)
    cfg = json.loads(p.read_text())
    cfg["agents"]["implementer"] = 7  # a hand-edited wrong type
    p.write_text(json.dumps(cfg))
    r = run(d, env, "models", "--json")
    out = text(r)
    if r.returncode == 0:  # the tolerant loader still runs: the view flags it on stderr with the file and key
        check("[CP4-X02] a wrong-typed stored value is flagged on stderr with the key and the file",
              "agents.implementer" in r.stderr and "config.json" in r.stderr, r.stderr[:300])
        check("[CP4-X02] stdout is still valid JSON with a detail list", "detail" in (jload(r) or {}), r.stdout[:200])
    else:  # or the loader refuses it: then it names the key and file and prints nothing on stdout
        check("[CP4-X02] a refused wrong-typed value names the key and the file and prints no JSON",
              "agents.implementer" in out and "config.json" in out and not r.stdout.strip() and "Traceback" not in out,
              out[:300])


def x03_default_json_old_fields_byte_identical():
    d, env = project({})
    r = run(d, env, "models", "--json")
    data = jload(r)
    old = json.dumps({"host": data.get("host"), "roles": data.get("roles"), "alternates": data.get("alternates")}, indent=2)
    want = json.dumps({"host": "anthropic", "roles": OLD_ROLES, "alternates": ["sonnet", "opus"]}, indent=2)
    check("[CP4-X03] the old fields serialize exactly as before", old == want, old[:200])


def x04_role_set_for_hosts_without_ui_providers_is_listed():
    d, env = project({})
    r = run(d, env, "models")
    check("[CP4-X04] the plain text lists a ui reviewer row too", "ui" in "\n".join(r.stdout.splitlines()[14:]).lower(),
          r.stdout[-300:])


def _cfg_for(name):
    paid = {"id": "paidy", "preset": "codex", "cost": "per-use", "model": "gpt-5"}
    return {"same": {"providers": [{"id": "claude", "preset": "claude"}], "agents": {"reviewer": "claude/opus"}},
            "paid": {"providers": [paid], "agents": {"reviewer": "paidy/gpt-5"}},
            "paid-ok": {"providers": [dict(paid, paid_ok_at="2026-01-01T00:00:00+00:00")],
                        "agents": {"reviewer": "paidy/gpt-5"}},
            "free": {"providers": [{"id": "codex", "preset": "codex"}], "agents": {"reviewer": "codex/gpt-5"}}}[name]


def x05_same_company_reviewer_not_effective():
    d, env = project(_cfg_for("same"))
    fake_clis(env)
    e = view(d, env)[1].get("reviewer") or {}
    check("[CP4-X05] a same-company reviewer gets the same-company reason and is not effective",
          "same company" in s(e.get("reason")).lower() and "claude" not in s(e.get("effective")).lower(), e)


def x06_paid_reviewer_needs_approval_then_usable():
    d, env = project(_cfg_for("paid"))
    fake_clis(env)
    e = view(d, env)[1].get("reviewer") or {}
    check("[CP4-X06] a paid reviewer without recorded consent needs approval and grants none",
          "approv" in s(e.get("reason")).lower() and "grants none" in s(e.get("reason"))
          and "paidy" not in s(e.get("effective")).lower(), e)
    d2, env2 = project(_cfg_for("paid-ok"))
    fake_clis(env2)
    e2 = view(d2, env2)[1].get("reviewer") or {}
    check("[CP4-X06] with consent recorded by the test it is the effective reviewer, with no reason",
          e2.get("reason") == "" and "gpt-5" in s(e2.get("effective")), e2)


def x07_ui_lens_applies_company_and_paid_rules():
    d, env = project({"ui_reviewers": {"visual": ["claude", "codex"]}})
    fake_clis(env)
    e = view(d, env)[1]["ui-reviewer-visual"]
    check("[CP4-X07] a same-company first UI choice is skipped with the same-company reason",
          e.get("effective") == "codex" and "same company" in s(e.get("reason")).lower(), e)
    paid = {"id": "codex", "preset": "codex", "cost": "per-use", "model": "gpt-5"}
    d2, env2 = project({"providers": [paid], "ui_reviewers": {"visual": ["codex"]}})
    fake_clis(env2)
    e2 = view(d2, env2)[1]["ui-reviewer-visual"]
    check("[CP4-X07] a per-use UI provider without consent is shown as the UI path really runs it, with the true note",
          e2.get("effective") == "codex" and "does not check paid consent" in s(e2.get("reason")), e2)
    d3, env3 = project({"providers": [dict(paid, paid_ok_at="2026-01-01T00:00:00+00:00")],
                        "ui_reviewers": {"visual": ["codex"]}})
    fake_clis(env3)
    e3 = view(d3, env3)[1]["ui-reviewer-visual"]
    check("[CP4-X07] with consent recorded by the test it is usable with no reason",
          e3.get("effective") == "codex" and not e3.get("reason"), e3)


def x08_models_agrees_with_real_reviewer_selection():
    for name in ("same", "paid"):  # the setting is not read by reviewer selection, so only exclusions can be compared
        cfg = _cfg_for(name)
        want = cfg["agents"]["reviewer"].split("/")[0]
        d, env = ac.approved(glob_before={"agents": cfg["agents"]}, providers=cfg["providers"])
        fake_clis(env, exit_code=0)
        e = view(d, env)[1].get("reviewer") or {}
        ids, _ = ac.to_review(d, env)
        shown = want in s(e.get("effective")).lower() or "gpt-5" in s(e.get("effective"))
        picked = any(i.startswith(want + "-") for i in ids)
        check(f"[CP4-X08] {name}: models says the outside reviewer is used exactly when reviewers would pick it",
              not shown or picked, (e, ids))
        check(f"[CP4-X08] {name}: an excluded provider is neither shown as effective nor picked",
              not shown and not picked, (e, ids))


TESTS = [(f"CP4-T{i:02d}", fn) for i, fn in enumerate((
    t01_every_role_listed, t02_old_fields_unchanged, t03_text_keeps_old_lines_adds_new, t04_project_source,
    t05_global_and_project, t06_unknown_tier_explained, t07_host_cannot_choose, t08_missing_host_entry,
    t09_reviewer_who_really_reviews, t10_unavailable_and_quota, t11_same_company_as_host, t12_paid_not_approved,
    t13_ui_reviewers, t14_orchestrator_director_never_switched, t15_contacts_nothing_spends_nothing,
    t16_bad_names_refused, t17_damaged_global_file, t18_defaults_match_today, t19_listing_changes_nobody,
    t20_readme_lists_file, t21_inventory_matches_what_the_file_reads), 1)]
TESTS += [("CP4-X01", x01_advisory_on_stderr_not_stdout), ("CP4-X02", x02_wrong_type_value_flagged_not_fatal),
          ("CP4-X03", x03_default_json_old_fields_byte_identical),
          ("CP4-X04", x04_role_set_for_hosts_without_ui_providers_is_listed),
          ("CP4-X05", x05_same_company_reviewer_not_effective),
          ("CP4-X06", x06_paid_reviewer_needs_approval_then_usable),
          ("CP4-X07", x07_ui_lens_applies_company_and_paid_rules),
          ("CP4-X08", x08_models_agrees_with_real_reviewer_selection)]


def x09_requested_keeps_the_tier_name():
    for level in ("project", "global"):
        for tier in ("light", "standard", "strong"):
            cfg = {"role_models": {"ratchet-implementer": tier}}
            d, env = project(cfg, glob={}) if level == "project" else project({}, glob=cfg)
            e = view(d, env)[1].get("implementer") or {}
            check(f"[CP4-X09] {level} role_models {tier}: requested keeps the tier name, effective is the resolved model",
                  e.get("requested") == tier and e.get("source") == level and not e.get("reason")
                  and e.get("effective") not in (None, tier), e)


TESTS += [("CP4-X09", x09_requested_keeps_the_tier_name)]


# ---- round 3: UI rows follow the UI review path's own rule; excluded reviewer request; role_models typo; default source

def _ui_env(env, key=None):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env.pop(k, None)
    if key:
        env["GEMINI_API_KEY"] = key


def x10_ui_rows_follow_the_ui_path_rule():
    cfg = {"ui_reviewers": {"visual": ["gemini", "codex"]}}
    d, env = project(cfg)
    fake_clis(env, names=("gemini", "codex"))
    _ui_env(env)
    e = view(d, env)[1]["ui-reviewer-visual"]
    check("[CP4-X10] gemini CLI present but no API key: gemini is not effective, codex is, with the key reason",
          e.get("effective") == "codex" and "GEMINI_API_KEY" in s(e.get("reason")), e)
    d2, env2 = project(cfg)
    fake_clis(env2, names=("codex",))
    _ui_env(env2, "k")
    e2 = view(d2, env2)[1]["ui-reviewer-visual"]
    check("[CP4-X10] API key set and no gemini CLI: gemini is effective with no reason",
          e2.get("effective") == "gemini" and not e2.get("reason"), e2)
    d3, env3 = project({"ui_reviewers": {"visual": ["claude", "codex", "gemini"]}})
    fake_clis(env3, names=("claude", "codex"))
    _ui_env(env3, "k")
    e3 = view(d3, env3)[1]["ui-reviewer-visual"]
    check("[CP4-X10] the host's own company (claude on an anthropic host) is excluded",
          e3.get("effective") == "codex" and "same company" in s(e3.get("reason")), e3)
    for order in (["codex", "gemini"], ["gemini", "codex"]):
        d4, env4 = project({"ui_reviewers": {"visual": order}})
        fake_clis(env4, names=("codex",))
        _ui_env(env4, "k")
        e4 = view(d4, env4)[1]["ui-reviewer-visual"]
        check(f"[CP4-X10] order {order}: the first ready runner in the listed order is effective",
              e4.get("effective") == order[0], e4)
    d5, env5 = project({"ui_reviewers": {"visual": ["opencode-glm", "codex"]}})
    fake_clis(env5)
    e5 = view(d5, env5)[1]["ui-reviewer-visual"]
    check("[CP4-X10] a name that is not a UI runner is dropped, as role_providers drops it",
          e5.get("effective") == "codex" and "not a UI review provider" in s(e5.get("reason")), e5)


def x11_excluded_reviewer_request_shows_the_real_fallback():
    for name in ("same", "paid"):
        cfg = _cfg_for(name)
        d, env = ac.approved(glob_before={"agents": cfg["agents"]}, providers=cfg["providers"])
        fake_clis(env, exit_code=0)
        e = view(d, env)[1].get("reviewer") or {}
        ids, _ = ac.to_review(d, env)
        check(f"[CP4-X11] {name}: the fallback shown is the automatic selection, never the session's own model "
              "while a different model is really picked",
              "session" not in s(e.get("effective")) and "best available" in s(e.get("effective")), (e, ids))
    d2, env2 = project({"agents": {"reviewer": "opus"}})
    e2 = view(d2, env2)[1].get("reviewer") or {}
    check("[CP4-X11] a bare model shows the same automatic text as the unset case",
          "session" not in s(e2.get("effective")) and "best available" in s(e2.get("effective")), e2)


def x12_role_models_typo_and_default_source():
    for bad_value in ("strng", "gpt-5"):
        d, env = project({"role_models": {"ratchet-implementer": bad_value}})
        e = view(d, env)[1].get("implementer") or {}
        check(f"[CP4-X12] role_models {bad_value!r} gets the unknown tier or model reason and still loads",
              "unknown tier or model" in s(e.get("reason")) and e.get("requested") == bad_value, e)
    d, env = project({"role_models": {"ratchet-implementer": "sonnet"}})
    e = view(d, env)[1].get("implementer") or {}
    check("[CP4-X12] a real model name has no reason", not e.get("reason"), e)
    d3, env3 = project({"ui_reviewers": {"visual": ["gemini", "claude", "codex"], "behavior": ["claude", "gemini", "codex"]}})
    e3 = view(d3, env3)[1]["ui-reviewer-visual"]
    check("[CP4-X12] a stored UI list equal to the shipped default reports source builtin", e3.get("source") == "builtin", e3)
    d4, env4 = project({"ui_reviewers": {"visual": ["codex"]}})
    e4 = view(d4, env4)[1]["ui-reviewer-visual"]
    check("[CP4-X12] a changed UI list reports source project", e4.get("source") == "project", e4)


TESTS += [("CP4-X10", x10_ui_rows_follow_the_ui_path_rule),
          ("CP4-X11", x11_excluded_reviewer_request_shows_the_real_fallback),
          ("CP4-X12", x12_role_models_typo_and_default_source)]


def x13_same_company_exclusion_across_provider_shapes():
    shapes = {
        "default providers": {"agents": {"reviewer": "claude/opus"}},
        "claude entry": {"providers": [{"id": "claude", "preset": "claude"}], "agents": {"reviewer": "claude/opus"}},
        "claude entry with company": {"providers": [{"id": "claude", "preset": "claude", "company": "anthropic"}],
                                      "agents": {"reviewer": "claude/opus"}},
        "id differs from preset": {"providers": [{"id": "myc", "preset": "claude"}], "agents": {"reviewer": "myc/opus"}},
        "id differs, company field": {"providers": [{"id": "myc", "command": ["claude"], "company": "Anthropic"}],
                                      "agents": {"reviewer": "myc/opus"}},
        "company from model name": {"providers": [{"id": "zz", "command": ["claude"], "model": "claude-opus-4"}],
                                    "agents": {"reviewer": "zz/claude-opus-4"}},
    }
    for name, cfg in shapes.items():
        d, env = project(cfg)
        fake_clis(env)
        e = view(d, env)[1].get("reviewer") or {}
        check(f"[CP4-X13] {name}: the host's own company is never effective; same-company reason and automatic text",
              "same company" in s(e.get("reason")).lower() and "best available" in s(e.get("effective"))
              and "claude" not in s(e.get("effective")).lower(), e)
    d, env = project({"providers": [{"id": "claude", "preset": "claude", "company": "openai"}],
                      "agents": {"reviewer": "claude/opus"}})
    fake_clis(env)
    e = view(d, env)[1].get("reviewer") or {}
    check("[CP4-X13] a provider whose company field is another company is a real outside reviewer, as selection treats it",
          not e.get("reason") and "opus" in s(e.get("effective")), e)


TESTS += [("CP4-X13", x13_same_company_exclusion_across_provider_shapes)]


def x14_one_marked_slot_is_reported_as_partial():
    d, env = project({"agents": {"reviewer": "codex/gpt-5"}})
    fake_clis(env)
    e = view(d, env)[1].get("reviewer") or {}
    check("[CP4-X14] every slot usable: no reason", not e.get("reason"), e)
    m = run(d, env, "reviewer-status", "--mark", "codex-standards", "--reason", "quota exceeded")
    e = view(d, env)[1].get("reviewer") or {}
    check("[CP4-X14] -both usable but the standards slot marked: the partial reason names it, codex stays effective",
          "code-standards slot falls back" in s(e.get("reason")) and "bugs slot" in s(e.get("reason"))
          and "codex" in s(e.get("effective")).lower(), (e, getattr(m, "stdout", m)))


TESTS += [("CP4-X14", x14_one_marked_slot_is_reported_as_partial)]


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
