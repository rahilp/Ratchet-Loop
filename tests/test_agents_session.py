#!/usr/bin/env python3
"""
CP7 tests: orchestrator and director, start-time model.   python3 tests/test_agents_session.py

A developer who picks a model for the orchestrator or the director (`agents.orchestrator`, `agents.director`, in the
machine-wide file or the project's config.json; the project's value wins) can see how to start a session on it. The
read-only `models` view (text and `--json`) shows, for each of the two roles that were chosen, the START COMMAND for the
agent tool: the host's own model flag for a bare model (`claude --model M`, `codex -m M`, `gemini -m M`,
`copilot --model M`, `opencode -m M`, the flags in PRESETS), and the chosen provider's own tool for a `provider/model`
value (openai/gpt-5 -> `codex -m gpt-5`). The director gets an instruction only. Ratchet never claims that a running
session switched: it says the choice applies to the next session Ratchet starts and that the current session's model
cannot be confirmed. `lane_start_command` (an argument list the director's `lanes-start` fills in and only prints) gains
a `{model}` placeholder, filled with the orchestrator's chosen model and empty when none is chosen; a command without
`{model}` prints exactly what it printed before. A badly written orchestrator model is refused where work starts
(`lanes-start`) with the valid names, while loading stays tolerant for a misspelled bare model (the models view shows it
as invalid). Choosing a model never approves paid use and never changes who reviews a step.

Design assumptions made where the production API does not exist yet (each is stated again at its case):
  * the start command is a plain string such as `claude --model claude-opus-4-1`, found in the orchestrator's (or
    director's) rows of the plain `models` text, and somewhere in that role's `detail` entry of `models --json` (the key
    name is not pinned); roles with no choice print none;
  * the plain text says `next session` and that Ratchet can't (or cannot) switch a running session, and that it can't
    (or cannot) confirm which model the current session runs on;
  * a refused lane start (`lanes-start`) names `agents.orchestrator`, the settings file and the valid model names.

Everything runs in-process through the engine's own entry points (standard 30): the engine module is loaded once per
process by tests/trust_helpers.py, each case builds its own throwaway git project with a private per-user Ratchet home
(helpers from tests/test_config_layers.py, tests/test_agents_reviewers.py and tests/test_models_explain.py). The
director's `lanes-start` runs in-process against a throwaway main checkout. No network, no real agent CLI, nothing is
spent; fake CLIs on a private PATH write a marker if they are ever run.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP7-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shlex
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
import test_agents_reviewers as ar                      # noqa: E402  (stub CLIs, world(), resolve(), approved_world())
import test_models_explain as me                        # noqa: E402  (OLD_ROLES, fake_clis(), ...)

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP7-[TX]\d+)\]")


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
ROLE_VAR = "RATCHET" + "_ROLE"          # the variable that marks the director's session (assembled: never typed whole)
HOSTS = {"claude": "anthropic", "codex": "openai", "gemini": "google", "copilot": "github", "opencode": "opencode"}
CMD_FORMS = {"claude": "claude --model {m}", "codex": "codex -m {m}", "gemini": "gemini -m {m}",
             "copilot": "copilot --model {m}", "opencode": "opencode -m {m}"}
OPUS = "claude-opus-4-1"                # a full model id: a known family, "not verified" but never a typo
SONNET45 = "claude-sonnet-4-5"
OPENAI = {"id": "openai", "preset": "codex", "model": "gpt-5"}
OPENAI_PAID = dict(OPENAI, cost="per-use")      # pay per use, and no paid_ok_at anywhere
START_ANYWHERE = re.compile(r"\b(?:claude --model|codex -m|gemini -m|copilot --model|opencode -m|cursor-agent --model) ")
SWITCH_CLAIM = re.compile(r"\bswitched\b|now using|applied to this session|now running", re.I)
NEXT_SESSION = re.compile(r"next session", re.I)
CANT_SWITCH = re.compile(r"can(?:no|['’])t switch a running", re.I)
CANT_CONFIRM = re.compile(r"can(?:no|['’])t confirm", re.I)
CURRENT_SESSION = re.compile(r"current session", re.I)
PLAIN_ROW = {"requested": "inherit", "effective": "inherit", "source": "builtin", "reason": ""}


def jload(r):
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def clean(env, host="claude"):
    """Point `env` at a host (claude, codex, gemini, copilot or opencode) and drop anything that could name the model
    of the machine's own session."""
    env["RATCHET_HOST_PROVIDER"] = HOSTS[host]
    for k in [k for k in env if "MODEL" in k.upper()]:
        env.pop(k)
    return env


def fresh(proj=None, glob=None, host="claude"):
    d, env = cl.project(proj if proj is not None else {}, glob=glob)
    return d, clean(env, host)


def rows(data):
    return {e["role"]: e for e in (data or {}).get("detail", []) if isinstance(e, dict) and "role" in e}


def models(d, env, *pre):
    """(parsed `models --json`, the plain `models` text, both calls' results)"""
    rj = run(d, env, *pre, "models", "--json")
    rt = run(d, env, *pre, "models")
    return jload(rj), rt.stdout, (rj, rt)


def about(out, role):
    """Everything the plain `models` text says about one role: its row with the lines under it, and any other line that
    names the role."""
    lines, block, on = out.splitlines(), [], False
    for ln in lines:
        if re.match(rf"^  {re.escape(role)}\s", ln):
            on, block = True, [ln]
        elif on and ln.startswith(" " * 20):
            block.append(ln)
        else:
            on = False
    rest = [ln for ln in lines if ln not in block and role in ln.lower()]
    return "\n".join(block + rest)


def jtext(v):
    return json.dumps(v)


def bytes_of(*paths):
    return [Path(p).read_bytes() if Path(p).exists() else None for p in paths]


def tree_text(*bases):
    return ar.tree_text(*bases)


# ---- a throwaway main checkout for the director's lanes-start, run in-process

class LaneWorld:
    def __init__(self, base, main, dfolder, env, denv):
        self.base, self.main, self.dfolder, self.env, self.denv = base, main, dfolder, env, denv
        self.marker = None

    def cfg_path(self):
        return self.main / ".ratchet" / "config.json"

    def start(self, lanes=("a=one",), name=None, js=False, request="Add search and export"):
        args = ["--root", str(self.main), "lanes-start", "--request", request]
        for ln in lanes:
            args += ["--lane", ln]
        if name:
            args += ["--name", name]
        if js:
            args.append("--json")
        return run(self.dfolder, self.denv, *args)

    def models(self, *a):
        return run(self.dfolder, self.env, "--root", str(self.main), "models", *a)

    def state(self):
        def git(*a):
            return subprocess.run(["git", *a], cwd=self.main, capture_output=True, text=True,
                                  env=dict(os.environ, PATH=th.SAFE_PATH)).stdout
        return {"branches": git("branch", "--list").split(), "worktrees": git("worktree", "list").count("\n"),
                "requests": sorted(p.name for p in (self.main / ".ratchet" / "requests").glob("*"))
                if (self.main / ".ratchet" / "requests").exists() else []}

    def wt(self, lane):
        return str(self.main.resolve().parent / f"{self.main.resolve().name}-{lane}")

    def brief(self, request_name, lane):
        return str(self.main.resolve() / ".ratchet" / "requests" / request_name / f"{lane}.md")


def lane_world(proj=None, glob=None, host="claude"):
    """A throwaway git project (its config.json committed), a director key, a fake `mycli` that writes a marker file if it
    is ever run, and a private per-user home. lanes-start is called as the director, from its own folder, with --root."""
    env = clean(th.make_env(), host)
    if glob is not None:
        cl.put_global(env, glob)
    bindir = Path(_tmp.mkdtemp(prefix="ratchet-agents-session-bin-"))
    marker = bindir / "mycli.marker"
    (bindir / "mycli").write_text(f"#!/bin/sh\necho \"$@\" >> '{marker}'\n")
    (bindir / "mycli").chmod(0o755)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    base = Path(os.path.realpath(_tmp.mkdtemp(prefix="ratchet-agents-session-")))
    main = base / "main"
    main.mkdir()
    for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        th.git(main, *c)
    (main / ".ratchet").mkdir()
    (main / ".ratchet" / "config.json").write_text(json.dumps(proj or {}))
    (main / "a.txt").write_text("x\n")
    th.git(main, "add", "-A")
    th.git(main, "commit", "-qm", "init")
    denv = dict(env)
    denv[ROLE_VAR] = "director"
    dfolder = Path(_tmp.mkdtemp(prefix="ratchet-agents-session-dir-"))
    w = LaneWorld(base, main, dfolder, env, denv)
    w.marker = marker
    r = run(dfolder, denv, "--root", str(main), "director-key")
    assert r.returncode == 0, text(r)
    return w


def lane_start_of(r, lane):
    """The `start` field of one lane from a `lanes-start --json` call, or None."""
    data = jload(r)
    return next((x.get("start") for x in (data or {}).get("lanes", []) if x.get("name") == lane), None)


TEMPLATE_MODEL = ["mycli", "--model", "{model}", "--cwd", "{worktree}"]
TEMPLATE_PLAIN = ["mycli", "--cwd", "{worktree}", "--brief", "{brief}", "--name", "{name}"]


# ------------------------------------------------------------------------------------------------ cases

def t01_orchestrator_start_command_on_claude_code():
    d, env = fresh({"agents": {"orchestrator": OPUS}})
    log = me.fake_clis(env)
    before = bytes_of(cl.project_path(d), cl.global_path(env))
    data, out, (rj, rt) = models(d, env)
    cmd = f"claude --model {OPUS}"
    row = rows(data).get("orchestrator", {})
    check("[CP7-T01] both views run", rj.returncode == 0 and rt.returncode == 0 and isinstance(data, dict), text(rj)[:300])
    check("[CP7-T01] the plain orchestrator line names the requested model, from the project",
          OPUS in about(out, "orchestrator") and "from project" in about(out, "orchestrator"), about(out, "orchestrator"))
    check("[CP7-T01] the plain orchestrator line prints the start command 'claude --model claude-opus-4-1'",
          cmd in about(out, "orchestrator"), about(out, "orchestrator"))
    check("[CP7-T01] --json: the orchestrator detail still asks for the model, from the project",
          row.get("requested") == OPUS and row.get("source") == "project", row)
    check("[CP7-T01] --json: the orchestrator detail carries the same command", cmd in jtext(row), row)
    check("[CP7-T01] the director (no choice) prints no start command and keeps its plain row",
          START_ANYWHERE.search(about(out, "director")) is None
          and {k: v for k, v in rows(data).get("director", {}).items() if k != "role"} == PLAIN_ROW, rows(data).get("director"))
    check("[CP7-T01] no provider CLI was run and nothing was written",
          not log.exists() and bytes_of(cl.project_path(d), cl.global_path(env)) == before, log)


def t02_director_start_instruction_only():
    d, env = fresh({"providers": [dict(OPENAI)]}, glob={"agents": {"director": "openai/gpt-5"}})
    log = me.fake_clis(env)
    data, out, (rj, rt) = models(d, env)
    row = rows(data).get("director", {})
    block = about(out, "director")
    cmd = "codex -m gpt-5"
    check("[CP7-T02] both views run", rj.returncode == 0 and rt.returncode == 0 and isinstance(data, dict), text(rj)[:300])
    check("[CP7-T02] the director line shows 'codex -m gpt-5'", cmd in block, block)
    check("[CP7-T02] it is an instruction for starting a new (next) director session",
          re.search(r"(?:new|next|fresh) (?:director )?session", block, re.I) is not None, block)
    check("[CP7-T02] it says Ratchet does not inspect the running director's model",
          re.search(r"(?:does not|doesn['’]t|never|can(?:no|['’])t) (?:inspect|confirm|see|check)", block, re.I) is not None, block)
    check("[CP7-T02] --json: the director detail asks for openai/gpt-5 from the global file and carries the command",
          row.get("requested") == "openai/gpt-5" and row.get("source") == "global" and cmd in jtext(row), row)
    check("[CP7-T02] the orchestrator (no choice) prints no start command",
          START_ANYWHERE.search(about(out, "orchestrator")) is None, about(out, "orchestrator"))
    check("[CP7-T02] no Paseo wording anywhere in either view", "paseo" not in (out + rj.stdout + text(rj)).lower(), out[-300:])
    check("[CP7-T02] no provider CLI was run", not log.exists(), log)
    d2, env2 = fresh({"providers": [dict(OPENAI)]}, glob={"agents": {"director": "openai/gpt-5"}}, host="codex")
    data2, out2, _ = models(d2, env2)
    check("[CP7-T02] the same command on a Codex host", cmd in about(out2, "director") and cmd in jtext(rows(data2).get("director")),
          about(out2, "director"))


HOST_MODEL = {"codex": "gpt-5", "gemini": "gemini-2.5-pro", "copilot": "gpt-5", "opencode": "gpt-5"}   # a model each host's tool runs


def t03_each_hosts_own_model_flag():
    engine = cl.engine()
    for host, form in CMD_FORMS.items():
        if host == "claude":
            continue
        m = HOST_MODEL[host]
        cmd = form.format(m=m)
        flag = engine.PRESETS[host if host != "opencode" else "opencode"]["model_flag"]
        check(f"[CP7-T03] ({host}) the expected command matches the PRESETS model flag", cmd.split()[1:2] == flag, (cmd, flag))
        d, env = fresh({"agents": {"orchestrator": m}}, host=host)
        data, out, (rj, rt) = models(d, env)
        row = rows(data).get("orchestrator", {})
        block = about(out, "orchestrator")
        check(f"[CP7-T03] ({host}) both views run", rj.returncode == 0 and rt.returncode == 0 and isinstance(data, dict), text(rj)[:300])
        check(f"[CP7-T03] ({host}) the plain orchestrator line prints '{cmd}'", cmd in block, block)
        check(f"[CP7-T03] ({host}) --json carries '{cmd}'", cmd in jtext(row), row)
        check(f"[CP7-T03] ({host}) never the claude form", "claude --model" not in block and "claude --model" not in jtext(row),
              block)
        others = [c.format(m=m) for h, c in CMD_FORMS.items() if h != host]
        check(f"[CP7-T03] ({host}) none of the other hosts' commands is printed",
              not any(o in block or o in jtext(row) for o in others), block)


def t04_never_claims_a_running_session_switched():
    d, env = fresh({"agents": {"orchestrator": "opus", "director": "sonnet"}})
    data, out, (rj, rt) = models(d, env)
    whole = out + "\n" + rj.stdout
    check("[CP7-T04] the view says the choice applies to the next session (orchestrator and director)",
          NEXT_SESSION.search(about(out, "orchestrator")) is not None and NEXT_SESSION.search(about(out, "director")) is not None,
          (about(out, "orchestrator"), about(out, "director")))
    check("[CP7-T04] the view says a running session can't be switched",
          CANT_SWITCH.search(about(out, "orchestrator")) is not None and CANT_SWITCH.search(about(out, "director")) is not None,
          about(out, "orchestrator"))
    check("[CP7-T04] --json: both details say next session too",
          all(NEXT_SESSION.search(jtext(rows(data).get(r, {}))) for r in ("orchestrator", "director")), rows(data).get("orchestrator"))
    check("[CP7-T04] the view never says switched, now using, applied to this session or now running",
          SWITCH_CLAIM.search(whole) is None, SWITCH_CLAIM.findall(whole))
    d2, env2 = fresh({})
    r1 = run(d2, env2, "config", "--set", "agents.orchestrator=opus")
    r2 = run(d2, env2, "config", "--set", "agents.director=sonnet", "--global")
    for label, r in (("orchestrator", r1), ("director", r2)):
        t = text(r)
        check(f"[CP7-T04] config --set agents.{label}: succeeds", r.returncode == 0, t[:300])
        check(f"[CP7-T04] config --set agents.{label}: says next session and that a running one can't be switched",
              NEXT_SESSION.search(t) is not None and CANT_SWITCH.search(t) is not None, t[:300])
        check(f"[CP7-T04] config --set agents.{label}: never claims a switch", SWITCH_CLAIM.search(t) is None, t[:300])


def t05_current_session_model_cannot_be_confirmed():
    d, env = fresh({"agents": {"orchestrator": OPUS}})
    data, out, (rj, rt) = models(d, env)
    row = rows(data).get("orchestrator", {})
    block = about(out, "orchestrator")
    check("[CP7-T05] the orchestrator line says Ratchet can't confirm which model the current session runs on",
          CANT_CONFIRM.search(block) is not None and CURRENT_SESSION.search(block) is not None, block)
    check("[CP7-T05] --json says the same in the detail",
          CANT_CONFIRM.search(jtext(row)) is not None and CURRENT_SESSION.search(jtext(row)) is not None, row)
    check("[CP7-T05] the requested model is not stated as the current one: effective is the session's own model",
          row.get("requested") == OPUS and OPUS not in str(row.get("effective")) and me.sessionish(row.get("effective")), row)
    check("[CP7-T05] the plain line does not say the session uses the requested model",
          re.search(rf"uses\s+{re.escape(OPUS)}|currently\s+(?:using|running)\s+{re.escape(OPUS)}", block) is None, block)


def t06_no_choice_leaves_output_unchanged():
    d, env = fresh({})
    data, out, (rj, rt) = models(d, env)
    check("[CP7-T06] host, roles and alternates are exactly today's",
          isinstance(data, dict) and data.get("host") == "anthropic" and data.get("roles") == me.OLD_ROLES
          and data.get("alternates") == ["sonnet", "opus"], jtext(data)[:300])
    check("[CP7-T06] the orchestrator and director details are today's rows, with no extra key",
          rows(data).get("orchestrator") == dict(PLAIN_ROW, role="orchestrator")
          and rows(data).get("director") == dict(PLAIN_ROW, role="director"), (rows(data).get("orchestrator"), rows(data).get("director")))
    check("[CP7-T06] the plain text keeps its header and prints no start command and no next-session note",
          out.startswith(me.OLD_HEADER) and START_ANYWHERE.search(out) is None and NEXT_SESSION.search(out) is None
          and NEXT_SESSION.search(rj.stdout) is None, out[:200])
    set_r = run(d, env, "config", "--set", "agents.implementer=opus")
    check("[CP7-T06] choosing a sub-agent role prints no session note",
          set_r.returncode == 0 and text(set_r).strip().endswith('Set agents.implementer = "opus"')
          and "session" not in text(set_r).lower(), text(set_r))
    w = lane_world({"lane_start_command": TEMPLATE_PLAIN})
    r = w.start(name="req", js=True)
    expect = shlex.join(["mycli", "--cwd", w.wt("a"), "--brief", w.brief("req", "a"), "--name", "a"])
    check("[CP7-T06] a lane start command with no choice anywhere is exactly today's, with no session note",
          r.returncode == 0 and lane_start_of(r, "a") == expect and NEXT_SESSION.search(text(r)) is None,
          (lane_start_of(r, "a"), expect, text(r)[-300:]))


def t07_lane_start_fills_model():
    w = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": OPUS}})
    r = w.start(lanes=("a=one", "b=two"), name="req-json", js=True)
    for lane in ("a", "b"):
        expect = shlex.join(["mycli", "--model", OPUS, "--cwd", w.wt(lane)])
        check(f"[CP7-T07] --json: lane {lane}'s start command is the template with {OPUS} and the worktree",
              r.returncode == 0 and lane_start_of(r, lane) == expect, (r.returncode, lane_start_of(r, lane), text(r)[-300:]))
    r2 = w.start(lanes=("c=three",), name="req-text")
    expect = shlex.join(["mycli", "--model", OPUS, "--cwd", w.wt("c")])
    check("[CP7-T07] text: the printed start command has the model and the worktree",
          r2.returncode == 0 and expect in r2.stdout.splitlines(), (r2.returncode, text(r2)[-400:]))
    check("[CP7-T07] no literal {model} is printed", "{model}" not in text(r) + text(r2), text(r2)[-300:])
    check("[CP7-T07] the command is only printed, never run", not w.marker.exists(), w.marker)


def t08_lane_start_without_model_unchanged():
    for label, proj in (("with an orchestrator choice", {"agents": {"orchestrator": OPUS}}), ("without one", {})):
        w = lane_world(dict(proj, lane_start_command=TEMPLATE_PLAIN))
        r = w.start(lanes=("a=one",), name="req", js=True)
        expect = shlex.join(["mycli", "--cwd", w.wt("a"), "--brief", w.brief("req", "a"), "--name", "a"])
        check(f"[CP7-T08] ({label}) --json start command is exactly the {{worktree}} {{brief}} {{name}} fill",
              r.returncode == 0 and lane_start_of(r, "a") == expect, (r.returncode, lane_start_of(r, "a"), expect))
        check(f"[CP7-T08] ({label}) the model is not added anywhere", OPUS not in str(lane_start_of(r, "a")), lane_start_of(r, "a"))
        r2 = w.start(lanes=("b=two",), name="req2")
        expect2 = shlex.join(["mycli", "--cwd", w.wt("b"), "--brief", w.brief("req2", "b"), "--name", "b"])
        check(f"[CP7-T08] ({label}) text output prints exactly that command", r2.returncode == 0 and expect2 in r2.stdout.splitlines(),
              text(r2)[-400:])
        check(f"[CP7-T08] ({label}) never run", not w.marker.exists(), w.marker)


def t09_model_empty_when_none_chosen():
    w = lane_world({"lane_start_command": ["mycli", "--model={model}", "--cwd", "{worktree}", "--name", "{name}"]})
    r = w.start(name="req", js=True)
    start = lane_start_of(r, "a")
    check("[CP7-T09] the command is accepted", r.returncode == 0 and start is not None, text(r)[-400:])
    parts = shlex.split(start or "")
    check("[CP7-T09] the {model} slot is empty, and the other placeholders still fill",
          parts == ["mycli", "--model=", "--cwd", w.wt("a"), "--name", "a"], parts)
    check("[CP7-T09] no literal {model} anywhere in the output", "{model}" not in text(r), text(r)[-300:])
    check("[CP7-T09] no crash (no traceback)", "Traceback" not in text(r), text(r)[-300:])
    r2 = w.start(lanes=("b=two",), name="req2")
    check("[CP7-T09] text mode too", r2.returncode == 0 and "{model}" not in text(r2)
          and shlex.join(["mycli", "--model=", "--cwd", w.wt("b"), "--name", "b"]) in r2.stdout.splitlines(), text(r2)[-400:])


def t10_validator_lists_every_placeholder():
    w = lane_world({"lane_start_command": ["mycli", "--branch", "{branch}"]})
    before = w.state()
    r = w.start()
    t = text(r)
    check("[CP7-T10] an unknown placeholder is refused", r.returncode != 0 and "lane_start_command" in t, t[:400])
    check("[CP7-T10] the message lists {worktree}, {brief}, {name} and {model}",
          all(p in t for p in ("{worktree}", "{brief}", "{name}", "{model}")), t[:500])
    check("[CP7-T10] nothing was created (no branch, worktree or request record)", w.state() == before, (before, w.state()))
    w2 = lane_world({"lane_start_command": ["mycli", "--model", "{model}"], "agents": {"orchestrator": OPUS}})
    r2 = w2.start(js=True)
    check("[CP7-T10] a command using {model} is accepted", r2.returncode == 0 and lane_start_of(r2, "a") is not None, text(r2)[:400])


def t11_badly_written_orchestrator_model_refused():
    for label, value in (("an empty string", ""), ("three parts", "a/b/c")):
        w = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": value}})
        before = w.state()
        rm = w.models()
        rs = w.start()
        for what, r in (("models", rm), ("lanes-start", rs)):
            t = text(r)
            check(f"[CP7-T11] ({label}) {what} stops", r.returncode != 0, t[:300])
            check(f"[CP7-T11] ({label}) {what} names the settings file and agents.orchestrator",
                  ".ratchet/config.json" in t and "agents.orchestrator" in t, t[:400])
            check(f"[CP7-T11] ({label}) {what} says what is valid (a model or provider/model)", "provider/model" in t, t[:400])
        check(f"[CP7-T11] ({label}) no lane, branch or worktree was created", w.state() == before, (before, w.state()))
    d, env = fresh({}, glob={"agents": {"orchestrator": "a/b/c"}})
    r = run(d, env, "models")
    check("[CP7-T11] a bad value in the machine-wide file names that file", r.returncode != 0
          and str(cl.global_path(env)) in text(r) and "agents.orchestrator" in text(r), text(r)[:300])
    # a misspelled bare model: loading stays tolerant, the work start refuses with the valid names
    w = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": "sonet"}})
    before = w.state()
    rm = w.models()
    out = rm.stdout
    check("[CP7-T11] (typo sonet) the models view still runs and shows the model as invalid, with no start command for it",
          rm.returncode == 0 and "sonet" in about(out, "orchestrator")
          and re.search(r"invalid|not a model Ratchet knows", about(out, "orchestrator")) is not None
          and START_ANYWHERE.search(about(out, "orchestrator")) is None, text(rm)[-600:])
    rs = w.start()
    t = text(rs)
    check("[CP7-T11] (typo sonet) lanes-start refuses naming the file, agents.orchestrator, the model and the valid names",
          rs.returncode != 0 and ".ratchet/config.json" in t and "agents.orchestrator" in t and "sonet" in t
          and "sonnet" in t and "opus" in t, t[:500])
    check("[CP7-T11] (typo sonet) no lane, branch or worktree was created", w.state() == before, (before, w.state()))


def t12_choosing_a_model_does_not_approve_paid_use():
    cfg = {"agents": {"orchestrator": "openai/gpt-5"}}
    providers = [dict(p) for p in ar.ROSTER] + [dict(OPENAI_PAID)]
    d, env, stubs = ar.world(cfg, providers=providers)
    base_d, base_env, _ = ar.world({}, providers=[dict(p) for p in ar.ROSTER] + [dict(OPENAI_PAID)])
    data, out, (rj, rt) = models(d, env)
    row = rows(data).get("orchestrator", {})
    block = about(out, "orchestrator")
    check("[CP7-T12] the view shows the payment-approval note for the orchestrator",
          re.search(r"paid approval|approval|exact OK", block) is not None and re.search(r"per use|pay", block, re.I) is not None, block)
    check("[CP7-T12] --json: the orchestrator detail says it needs approval too",
          re.search(r"paid approval|approval|exact OK", jtext(row)) is not None, row)
    blob = tree_text(d / ".ratchet", env["RATCHET_HOME"])
    check("[CP7-T12] paid_ok_at is unset in every config file and record", "paid_ok_at" not in blob and "paid_ok_at" not in
          jtext(cl.effective(d, env)[0]), blob.count("paid_ok_at"))
    got, base = ar.resolve(d, env, "normal"), ar.resolve(base_d, base_env, "normal")
    check("[CP7-T12] the reviewer pick (ids, slots, fallbacks, reasons) is the same as without the setting",
          got.out is not None and got.shape() == base.shape() and len(got.ids) >= 1, (got.ids, base.ids, got.err[-200:]))
    check("[CP7-T12] the paid provider is not picked, and the host's own company is still never an outside reviewer",
          not any(i.startswith("openai") for i in got.ids) and not any(i.startswith("claude") for i in got.ids), got.ids)
    d2, env2 = fresh({"providers": [dict(OPENAI_PAID)]}, glob={"agents": {"director": "openai/gpt-5"}})
    out2 = models(d2, env2)[1]
    check("[CP7-T12] a director choice in the machine-wide file shows the note and writes no consent",
          re.search(r"paid approval|approval|exact OK", about(out2, "director")) is not None
          and "paid_ok_at" not in tree_text(d2 / ".ratchet", env2["RATCHET_HOME"]), about(out2, "director"))


def t13_orchestrator_model_changes_no_reviewer_rules():
    cfg = {"providers": [dict(ar.GEMINI), dict(ar.CODEX), dict(ar.PAID)]}
    glob = {"agents": {"orchestrator": "opus", "director": "sonnet"}}
    runs = []
    for label, g in (("without", None), ("with", glob)):
        d, env, stubs, rnd = ar.approved_world(dict(cfg), glob=g, cp_extra={"risk": "normal"})
        clean(env)
        summary = cl.run(d, env, "plan-summary")
        effective = cl.effective(d, env)[0] or {}
        view = cl.run(d, env, "models")
        lines = [ln.replace(str(d), "<D>") for ln in summary.stdout.splitlines() if not ln.startswith("Models:")]
        runs.append({"ids": [x["id"] for x in rnd.get("reviewers", [])], "lines": lines, "summary_rc": summary.returncode,
                     "depth": effective.get("review_depth"), "blocking": effective.get("blocking_severities"), "view": view.stdout})
    a, b = runs
    check("[CP7-T13] a normal-risk step gets reviewers in both runs", len(a["ids"]) == 2 and a["summary_rc"] == 0 == b["summary_rc"], a["ids"])
    check("[CP7-T13] reviewer picks and reviewer count are identical", a["ids"] == b["ids"], (a["ids"], b["ids"]))
    check("[CP7-T13] review depth and blocking rules are identical (and are the shipped ones)",
          a["depth"] == b["depth"] == ar.DEFAULT_REVIEW_DEPTH and a["blocking"] == b["blocking"] == ar.DEFAULT_BLOCKING,
          (a["depth"], b["depth"], a["blocking"], b["blocking"]))
    check("[CP7-T13] plan-summary is identical apart from its Models line", a["lines"] == b["lines"] and len(a["lines"]) > 3,
          [x for x in b["lines"] if x not in a["lines"]][:3])
    check("[CP7-T13] only the models view differs: it shows the session start commands when they are chosen",
          START_ANYWHERE.search(a["view"]) is None and f"claude --model opus" in about(b["view"], "orchestrator")
          and f"claude --model sonnet" in about(b["view"], "director"), about(b["view"], "orchestrator"))


def t14_project_choice_overrides_global():
    a_model, b_model = OPUS, SONNET45
    w = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": b_model}},
                   glob={"agents": {"orchestrator": a_model}})
    data = jload(w.models("--json"))
    out = w.models().stdout
    row = rows(data).get("orchestrator", {})
    check("[CP7-T14] the view shows the project's model, from the project",
          row.get("requested") == b_model and row.get("source") == "project" and f"claude --model {b_model}" in jtext(row)
          and "from project" in about(out, "orchestrator") and b_model in about(out, "orchestrator"), row)
    check("[CP7-T14] the view does not show the global model", a_model not in about(out, "orchestrator")
          and a_model not in jtext(row), about(out, "orchestrator"))
    r = w.start(name="first", js=True)
    check("[CP7-T14] lanes-start uses the project's model",
          r.returncode == 0 and lane_start_of(r, "a") == shlex.join(["mycli", "--model", b_model, "--cwd", w.wt("a")]),
          (lane_start_of(r, "a"), text(r)[-300:]))
    cfg = json.loads(w.cfg_path().read_text())
    cfg.pop("agents")
    w.cfg_path().write_text(json.dumps(cfg))
    data = jload(w.models("--json"))
    out = w.models().stdout
    row = rows(data).get("orchestrator", {})
    check("[CP7-T14] after removing the project value the view shows the global model, from the global file",
          row.get("requested") == a_model and row.get("source") == "global" and f"claude --model {a_model}" in jtext(row)
          and "from global" in about(out, "orchestrator") and b_model not in about(out, "orchestrator"), row)
    r2 = w.start(lanes=("b=two",), name="second", js=True)
    check("[CP7-T14] and lanes-start uses the global model",
          r2.returncode == 0 and lane_start_of(r2, "b") == shlex.join(["mycli", "--model", a_model, "--cwd", w.wt("b")]),
          (lane_start_of(r2, "b"), text(r2)[-300:]))


def t15_readme_lists_the_new_file():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("### Running the tests", 1)[-1]
    blocks = re.findall(r"```[^\n]*\n(.*?)```", section, re.S)
    home = [b for b in blocks if re.search(r"^python3 tests/test_agents_reviewers\.py$", b, re.M)]
    check("[CP7-T15] the 'Running the tests' block that lists python3 tests/test_agents_reviewers.py exists", len(home) == 1, len(home))
    lines = home[0].splitlines() if home else []
    mine = "python3 tests/test_agents_session.py"
    check("[CP7-T15] that block lists python3 tests/test_agents_session.py exactly once", lines.count(mine) == 1, lines.count(mine))
    i = lines.index(mine) if mine in lines else -2
    check("[CP7-T15] it sits in alphabetical place: right after test_agents_reviewers.py and before test_brief.py",
          i > 0 and lines[i - 1] == "python3 tests/test_agents_reviewers.py" and lines[i + 1:i + 2] == ["python3 tests/test_brief.py"],
          lines[max(i - 1, 0):i + 2])


def t16_inventory_matches_what_the_file_reads():
    src = Path(__file__).read_text()
    # Patterns assembled from pieces so this file's own source never matches them by accident.
    load_pat = re.compile("spec_from_file" + "_location")
    src_pat = re.compile("ENGINE" + r"\.read_text|ast" + r"\.parse|ast" + r"\.walk|get" + "source")
    scans = bool(load_pat.search(src) or src_pat.search(src))
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    listed = "test_agents_session.py" in inv
    check("[CP7-T16] the file is in the test inventory exactly when it reads engine source or loads it by spec "
          "(it does neither: it drives the engine through the shared loader, so it must not be listed)",
          scans == listed, f"scans={scans} listed={listed}")
    check("[CP7-T16] the inventory fixture and its checking test exist",
          (ROOT / "tests/test_modularize_inventory.py").is_file(), None)


def t17_full_suite_gate():
    # Gate, not a unit assertion: `ratchet.py complete` runs the whole local suite itself and refuses to finish this
    # step if it fails (it is not re-read here: regression.log is written only after this file has run).
    suites = sorted(p.name for p in HERE.glob("test_*.py") if p.is_file())      # run_all.py's own discovery rule
    check("[CP7-T17] tests/run_all.py discovers this file (test_*.py directly under tests/)",
          Path(__file__).name in suites and (HERE / "run_all.py").is_file(), Path(__file__).name)
    if os.environ.get("RATCHET_CP7_T17_FULL_SUITE") == "1":
        child_env = {k: v for k, v in os.environ.items() if k != "RATCHET_CP7_T17_FULL_SUITE"}
        r = subprocess.run([sys.executable, "tests/run_all.py"], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=5400, env=child_env)
        m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
        failed = int(m.group(2)) if m else -1
        check("[CP7-T17] python3 tests/run_all.py exits 0 with 0 failed", r.returncode == 0 and failed == 0,
              (r.returncode, r.stdout[-400:]))
    else:
        check("[CP7-T17] the full suite is enforced structurally by `ratchet.py complete`, which refuses to mark a step done "
              "when its own regression run fails; not re-run here by default (set RATCHET_CP7_T17_FULL_SUITE=1 to run it "
              "inline)", True)


NOT_RUNNABLE = re.compile(r"can(?:no|['’])t run|cannot run", re.I)
EMPTY_NOTE = re.compile(r"\bempty\b", re.I)
PAID_NOTE = re.compile(r"paid approval|approval|exact OK|costs money|per use", re.I)
OWNER_HOST = {"gpt-5": "codex", "gemini-2.5-pro": "gemini", "claude-opus-4-1": "claude", "sonnet": "claude"}


def t18_unrunnable_model_paid_and_empty_model_notes():
    # (i) a bare model of a company the host's tool does not run: shown, said plainly, no mixed start command
    cases = [("claude", "gpt-5"), ("claude", "gemini-2.5-pro"), ("codex", "claude-opus-4-1"), ("codex", "sonnet"),
             ("gemini", "claude-opus-4-1"), ("gemini", "sonnet")]
    for host, m in cases:
        d, env = fresh({"agents": {"orchestrator": m, "director": m}}, host=host)
        data, out, (rj, rt) = models(d, env)
        check(f"[CP7-T18] ({host}, {m}) both views run", rj.returncode == 0 and rt.returncode == 0 and isinstance(data, dict),
              text(rj)[:300] + text(rt)[:300])
        forbidden = [CMD_FORMS[h].format(m=m) for h in CMD_FORMS if h != OWNER_HOST[m]]
        for role in ("orchestrator", "director"):
            block, row = about(out, role), rows(data).get(role, {})
            tag = f"[CP7-T18] ({host}, {m}, {role})"
            check(f"{tag} the row shows the choice", f"asked {m}" in block and row.get("requested") == m, block)
            check(f"{tag} it says plainly that this host's tool cannot run that model",
                  NOT_RUNNABLE.search(block) is not None and NOT_RUNNABLE.search(jtext(row)) is not None, block)
            check(f"{tag} no start command mixes the host's tool with the model (text and --json)",
                  not any(f in block or f in jtext(row) for f in forbidden), block)
            pair = ("openai/gpt-5" in block) if m == "gpt-5" else (f"/{m}" in block or "provider/model" in block)
            check(f"{tag} it points to the provider/model form", pair, block)
            check(f"{tag} it never claims a switch", SWITCH_CLAIM.search(block) is None, block)
    d, env = fresh({"agents": {"orchestrator": "opus"}})
    blk = about(models(d, env)[1], "orchestrator")
    check("[CP7-T18] (control) a model the host's tool runs still prints its start command and no 'cannot run'",
          "claude --model opus" in blk and NOT_RUNNABLE.search(blk) is None, blk)
    d, env = fresh({"agents": {"orchestrator": "gpt-5"}}, host="codex")
    blk = about(models(d, env)[1], "orchestrator")
    check("[CP7-T18] (control) gpt-5 on codex still prints 'codex -m gpt-5' and no 'cannot run'",
          "codex -m gpt-5" in blk and NOT_RUNNABLE.search(blk) is None, blk)

    # (ii) provider/model on a per-use provider whose paid OK covers a different model
    engine = cl.engine()
    paid = {"id": "codexpaid", "preset": "codex", "cost": "per-use", "model": "gpt-5", "paid_ok_at": "2026-01-01T00:00:00+00:00"}
    judged = engine.per_use_unapproved(dict(paid, launch_model="gpt-5-pro"))
    covered = engine.per_use_unapproved(dict(paid, launch_model="gpt-5"))
    check("[CP7-T18] (the reviewer path) judges the other model unapproved and the covered one approved",
          judged is True and covered is False, (judged, covered))
    d, env = fresh({"providers": [dict(paid)], "agents": {"orchestrator": "codexpaid/gpt-5-pro", "director": "codexpaid/gpt-5-pro"}})
    data, out, _ = models(d, env)
    for role in ("orchestrator", "director"):
        block, row = about(out, role), rows(data).get(role, {})
        note = re.search(r"(?:exact OK|approval)[^\n]{0,60}gpt-5-pro", block) or re.search(r"gpt-5-pro[^\n]{0,40}(?:exact OK|approval)", block)
        check(f"[CP7-T18] (paid OK for gpt-5, choice codexpaid/gpt-5-pro) the {role} row prints the paid-approval note naming gpt-5-pro",
              note is not None, block)
        check(f"[CP7-T18] (same) --json: the {role} detail carries the note", PAID_NOTE.search(jtext(row)) is not None, row)
    d2, env2 = fresh({"providers": [dict(paid)], "agents": {"orchestrator": "codexpaid/gpt-5", "director": "codexpaid/gpt-5"}})
    data2, out2, _ = models(d2, env2)
    for role in ("orchestrator", "director"):
        check(f"[CP7-T18] (paid OK covers the chosen gpt-5) the {role} row prints no paid-approval note",
              PAID_NOTE.search(about(out2, role)) is None and PAID_NOTE.search(jtext(rows(data2).get(role, {}))) is None,
              about(out2, role))
        check(f"[CP7-T18] (same) the {role} row still prints its start command", "codex -m gpt-5" in about(out2, role),
              about(out2, role))

    # (iii) lanes-start with {model} as its own word and no orchestrator model chosen
    w = lane_world({"lane_start_command": TEMPLATE_MODEL})
    r = w.start(name="req", js=True)
    start = lane_start_of(r, "a")
    check("[CP7-T18] (empty {model} word) the command is still printed, with an empty model word",
          r.returncode == 0 and shlex.split(start or "") == ["mycli", "--model", "", "--cwd", w.wt("a")], (start, text(r)[-300:]))
    r2 = w.start(lanes=("b=two",), name="req2")
    cmd = shlex.join(["mycli", "--model", "", "--cwd", w.wt("b")])
    notes = [ln for ln in r2.stdout.splitlines() if ln != cmd and EMPTY_NOTE.search(ln)]
    check("[CP7-T18] (empty {model} word) text: the command is printed and a plain note says the model argument is empty because "
          "no orchestrator model is chosen", r2.returncode == 0 and cmd in r2.stdout.splitlines()
          and any("no orchestrator model" in ln for ln in notes), text(r2)[-500:])
    check("[CP7-T18] (empty {model} word) the command is only printed, never run", not w.marker.exists(), w.marker)
    w2 = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": OPUS}})
    r3 = w2.start(name="req")
    check("[CP7-T18] (a model is chosen) no empty-argument note", r3.returncode == 0 and EMPTY_NOTE.search(r3.stdout) is None
          and "no orchestrator model" not in r3.stdout, r3.stdout[-300:])
    w3 = lane_world({"lane_start_command": TEMPLATE_PLAIN})
    r4 = w3.start(name="req")
    expect = shlex.join(["mycli", "--cwd", w3.wt("a"), "--brief", w3.brief("req", "a"), "--name", "a"])
    check("[CP7-T18] (a template without {model}) prints its command and no note about an empty model",
          r4.returncode == 0 and expect in r4.stdout.splitlines() and EMPTY_NOTE.search(r4.stdout) is None
          and "no orchestrator model" not in r4.stdout and "model argument" not in r4.stdout, r4.stdout[-400:])


def tx01_session_model_not_offered_by_provider():
    prov = {"id": "codex", "preset": "codex", "model": "gpt-5.1", "models": ["gpt-5.1"]}
    # a provider/model whose provider declares a models list without that model: invalid, no start command, refused
    w = lane_world({"lane_start_command": TEMPLATE_MODEL, "providers": [prov], "agents": {"orchestrator": "codex/gpt-9"}})
    before = w.state()
    rm = w.models()
    rj = w.models("--json")
    check("[CP7-X01] the models view still runs", rm.returncode == 0 == rj.returncode, text(rm)[-400:])
    blk = about(rm.stdout, "orchestrator")
    check("[CP7-X01] the view shows the choice as invalid / not offered by the provider",
          "gpt-9" in blk and "invalid" in blk and "not offered by codex" in blk and "gpt-5.1" in blk, blk[:500])
    check("[CP7-X01] no `codex -m gpt-9` start command anywhere in the text or --json view",
          "codex -m gpt-9" not in rm.stdout and "codex -m gpt-9" not in rj.stdout and START_ANYWHERE.search(blk) is None,
          blk[:500])
    row = rows(jload(rj)).get("orchestrator") or {}
    check("[CP7-X01] --json: the orchestrator row has no start and says not offered",
          not row.get("start") and "not offered by codex" in jtext(row), jtext(row)[:400])
    rs = w.start()
    t = text(rs)
    check("[CP7-X01] lanes-start refuses naming the setting, the model, the provider and the offered list",
          rs.returncode != 0 and "agents.orchestrator" in t and "gpt-9" in t and "codex" in t and "gpt-5.1" in t, t[:500])
    check("[CP7-X01] nothing was created", w.state() == before, (before, w.state()))
    # a model the provider lists prints its start command and lanes-start fills {model}
    w2 = lane_world({"lane_start_command": TEMPLATE_MODEL, "providers": [prov], "agents": {"orchestrator": "codex/gpt-5.1"}})
    b2 = about(w2.models().stdout, "orchestrator")
    check("[CP7-X01] a listed model prints its start command", "codex -m gpt-5.1" in b2 and "invalid" not in b2, b2[:400])
    r2 = w2.start(js=True)
    check("[CP7-X01] and lanes-start accepts it", r2.returncode == 0 and "gpt-5.1" in (lane_start_of(r2, "a") or ""), text(r2)[:300])
    # a provider that declares no list: not verified, behaves as before
    w3 = lane_world({"lane_start_command": TEMPLATE_MODEL, "providers": [{k: v for k, v in prov.items() if k != "models"}],
                     "agents": {"orchestrator": "codex/gpt-9"}})
    b3 = about(w3.models().stdout, "orchestrator")
    check("[CP7-X01] a provider declaring no list passes the model through (start command shown)",
          "codex -m gpt-9" in b3 and "invalid" not in b3, b3[:400])


def tx02_unknown_company_is_refused_where_work_starts():
    unknown = re.compile(r"unknown provider|no provider|not a (?:known )?provider|provider[^\n]{0,30}not known|not known", re.I)
    valid = ("claude", "codex", "gemini")
    for level in ("project", "global"):
        for value in ("acme/gpt-5", "acme/claude-opus-4-1"):
            pick = {"orchestrator": value, "director": value}
            if level == "project":
                w = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": dict(pick)})
                where = ".ratchet/config.json"
            else:
                w = lane_world({"lane_start_command": TEMPLATE_MODEL}, glob={"agents": dict(pick)})
                where = str(cl.global_path(w.env))
            tag = f"[CP7-X02] ({level}, {value})"

            def R(*a, w=w):
                return run(w.dfolder, w.env, "--root", str(w.main), *a)
            rc, rm, rj = R("config"), R("models"), R("models", "--json")
            for label, r in (("config", rc), ("models", rm), ("models --json", rj)):
                t = text(r)
                check(f"{tag} load refuses: `{label}` exits 1 naming the file, agents.<role>, acme and the valid providers",
                      r.returncode == 1 and where in t and re.search(r"agents\.(?:orchestrator|director)", t) is not None
                      and "acme" in t and all(v in t for v in valid), (r.returncode, t[:400]))
            refusal = text(rc) + "\n" + text(rm) + "\n" + text(rj)
            check(f"{tag} the refusal says the provider is not known and prints no start command at all (orchestrator or director)",
                  unknown.search(text(rm)) is not None and START_ANYWHERE.search(refusal) is None
                  and SWITCH_CLAIM.search(refusal) is None, refusal[:500])
            for what, tmpl in (("with {model}", TEMPLATE_MODEL), ("without {model}", TEMPLATE_PLAIN)):
                if level == "project":
                    w2 = lane_world({"lane_start_command": tmpl, "agents": dict(pick)})
                else:
                    w2 = lane_world({"lane_start_command": tmpl}, glob={"agents": dict(pick)})
                before = w2.state()
                r = w2.start()
                t = text(r)
                where = ".ratchet/config.json" if level == "project" else str(cl.global_path(w2.env))
                check(f"{tag} lanes-start {what} refuses naming agents.orchestrator, acme, the file and the valid choices",
                      r.returncode != 0 and "agents.orchestrator" in t and "acme" in t and where in t
                      and all(v in t for v in valid), t[:500])
                check(f"{tag} lanes-start {what} created nothing and ran no command", w2.state() == before and not w2.marker.exists(),
                      (before, w2.state()))
            # repair: only the orchestrator setting is bad here, so `config --unset agents.orchestrator` must work on its own
            if level == "project":
                w3 = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": value}})
            else:
                w3 = lane_world({"lane_start_command": TEMPLATE_MODEL}, glob={"agents": {"orchestrator": value}})
            g = ["--global"] if level == "global" else []
            check(f"{tag} before the repair the load is refused", w3.models().returncode == 1, text(w3.models())[:200])
            r1 = run(w3.dfolder, w3.env, "--root", str(w3.main), "config", *g, "--unset", "agents.orchestrator")
            rafter = w3.models()
            check(f"{tag} the repair command exits 0 and the models view is plain afterwards",
                  r1.returncode == 0 and rafter.returncode == 0, (text(r1)[:200], text(rafter)[:200]))
            check(f"{tag} and the bad value is gone from the file",
                  b"acme" not in b"".join(x or b"" for x in bytes_of(w3.cfg_path(), cl.global_path(w3.env))),
                  bytes_of(w3.cfg_path(), cl.global_path(w3.env)))
    # (d) config --set refuses the unknown company, writes nothing, lists the valid choices
    for level in ("project", "global"):
        w = lane_world({"lane_start_command": TEMPLATE_MODEL}, glob=({} if level == "global" else None))
        paths = [w.cfg_path(), cl.global_path(w.env)]
        before = bytes_of(*paths)
        args = ["--root", str(w.main), "config"] + (["--global"] if level == "global" else []) + ["--set", "agents.orchestrator=acme/gpt-5"]
        r = run(w.dfolder, w.env, *args)
        t = text(r)
        check(f"[CP7-X02] ({level}) config --set agents.orchestrator=acme/gpt-5 is refused, naming acme and the valid choices",
              r.returncode != 0 and "acme" in t and all(v in t for v in valid), t[:400])
        check(f"[CP7-X02] ({level}) and nothing was written", bytes_of(*paths) == before, (before, bytes_of(*paths)))
    # (d2) a bare company name is accepted by config --set (the provider part may be a company from the built-in presets)
    for level in ("project", "global"):
        w = lane_world({"lane_start_command": TEMPLATE_MODEL}, glob=({} if level == "global" else None))
        args = ["--root", str(w.main), "config"] + (["--global"] if level == "global" else []) + ["--set", "agents.orchestrator=openai/gpt-5"]
        r = run(w.dfolder, w.env, *args)
        written = b"openai/gpt-5" in b"".join(x or b"" for x in bytes_of(w.cfg_path(), cl.global_path(w.env)))
        check(f"[CP7-X02] ({level}) config --set agents.orchestrator=openai/gpt-5 succeeds and is written", r.returncode == 0 and written,
              (r.returncode, text(r)[:300]))
    # (e) controls: a bare company name and a configured provider id still resolve to a start command
    for label, proj, val in (("bare company openai, no provider with that id", {}, "openai/gpt-5"),
                             ("built-in provider id codex", {}, "codex/gpt-5"),
                             ("configured provider id openai", {"providers": [dict(OPENAI)]}, "openai/gpt-5")):
        w = lane_world(dict(proj, lane_start_command=TEMPLATE_MODEL, agents={"orchestrator": val}))
        rm = w.models()
        check(f"[CP7-X02] control ({label}) models exits 0 and prints `codex -m gpt-5`",
              rm.returncode == 0 and "codex -m gpt-5" in about(rm.stdout, "orchestrator"), text(rm)[-400:])

    # (f) a known company whose tool cannot run the model is never given a mixed command
    w = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": "openai/" + OPUS}})
    rm, rj = w.models(), w.models("--json")
    mixed = [ln for ln in (rm.stdout + "\n" + rj.stdout).splitlines() if START_ANYWHERE.search(ln) and OPUS in ln]
    check(f"[CP7-X02] openai/{OPUS} gets no start command naming that model (no codex/claude mix)", not mixed, mixed[:3])

def tx03_custom_command_provider_gets_no_built_in_start_command():
    """[CP7-X03] A configured provider that runs its own command (no preset) never gets a built-in tool's start command
    derived from its company: the choice is shown, no command is printed, and Ratchet says it cannot build one."""
    own_cmd = ["mycli", "--model", "{model}", "{prompt_file}"]
    cannot = re.compile(r"cannot build a session start command from provider \S+'s own command", re.I)
    for label, pv, val in (("id myprov, company openai", {"id": "myprov", "company": "openai", "model": "gpt-5", "command": own_cmd},
                            "myprov/gpt-5"),
                           ("id openai with its own command", {"id": "openai", "model": "gpt-5", "command": own_cmd},
                            "openai/gpt-5")):
        for role in ("orchestrator", "director"):
            w = lane_world({"providers": [pv], "lane_start_command": TEMPLATE_MODEL, "agents": {role: val}})
            rm, rj = w.models(), w.models("--json")
            both = rm.stdout + "\n" + rj.stdout
            check(f"[CP7-X03] ({label}, {role}) models exits 0 and prints no built-in start command",
                  rm.returncode == 0 and START_ANYWHERE.search(both) is None, text(rm)[-400:])
            check(f"[CP7-X03] ({label}, {role}) it says Ratchet cannot build the start command from that provider's own command",
                  cannot.search(rm.stdout) is not None and cannot.search(rj.stdout) is not None, text(rm)[-500:])
            check(f"[CP7-X03] ({label}, {role}) it still shows the choice and claims no switch",
                  val in about(rm.stdout, role) and SWITCH_CLAIM.search(both) is None, about(rm.stdout, role))
    # controls: a preset-id provider and a bare company name keep their start command
    for label, proj, val in (("configured provider id codex with a preset", {"providers": [{"id": "codex", "preset": "codex"}]}, "codex/gpt-5"),
                             ("bare company openai", {}, "openai/gpt-5")):
        w = lane_world(dict(proj, lane_start_command=TEMPLATE_MODEL, agents={"orchestrator": val}))
        rm = w.models()
        check(f"[CP7-X03] control ({label}) still prints `codex -m gpt-5`",
              rm.returncode == 0 and "codex -m gpt-5" in about(rm.stdout, "orchestrator"), text(rm)[-400:])

def tx04_inherit_means_no_model_argument():
    """[CP7-X04] `inherit` (alone or as provider/inherit) is the session's own model: the orchestrator/director row prints no
    start command and no cannot-run note, and lanes-start fills {model} with an empty word plus the empty-model note."""
    for host in ("claude", "codex", "gemini"):
        for val in ("inherit", "anthropic/inherit"):
            d, env = fresh({"agents": {"orchestrator": val, "director": val}}, host=host)
            data, out, (rj, rt) = models(d, env)
            check(f"[CP7-X04] ({host}, {val}) both views run", rj.returncode == 0 and rt.returncode == 0, text(rj)[:300])
            for role in ("orchestrator", "director"):
                block, row = about(out, role), rows(data).get(role, {})
                tag = f"[CP7-X04] ({host}, {val}, {role})"
                check(f"{tag} prints no start command and no cannot-run note (text and --json)",
                      START_ANYWHERE.search(block) is None and START_ANYWHERE.search(jtext(row)) is None
                      and NOT_RUNNABLE.search(block) is None and NOT_RUNNABLE.search(jtext(row)) is None
                      and not row.get("start"), block)
                check(f"{tag} says it starts on the session's own model and still claims no switch",
                      "session's own model" in block and CANT_SWITCH.search(block) is not None
                      and SWITCH_CLAIM.search(block) is None, block)
    for val in ("inherit", "anthropic/inherit"):
        w = lane_world({"lane_start_command": TEMPLATE_MODEL, "agents": {"orchestrator": val}})
        r = w.start(name="req", js=True)
        start = lane_start_of(r, "a")
        check(f"[CP7-X04] (lanes-start, {val}) {{model}} is filled with an empty word, never `inherit`",
              r.returncode == 0 and shlex.split(start or "") == ["mycli", "--model", "", "--cwd", w.wt("a")], (start, text(r)[-300:]))
        r2 = w.start(lanes=("b=two",), name="req2")
        check(f"[CP7-X04] (lanes-start, {val}) the empty-model note is printed",
              r2.returncode == 0 and "inherit" not in r2.stdout and EMPTY_NOTE.search(r2.stdout) is not None, text(r2)[-400:])
    d, env = fresh({"agents": {"orchestrator": "opus"}})
    check("[CP7-X04] (control) opus still prints `claude --model opus`",
          "claude --model opus" in about(models(d, env)[1], "orchestrator"), about(models(d, env)[1], "orchestrator"))


TESTS = [
    ("CP7-T01", t01_orchestrator_start_command_on_claude_code),
    ("CP7-T02", t02_director_start_instruction_only),
    ("CP7-T03", t03_each_hosts_own_model_flag),
    ("CP7-T04", t04_never_claims_a_running_session_switched),
    ("CP7-T05", t05_current_session_model_cannot_be_confirmed),
    ("CP7-T06", t06_no_choice_leaves_output_unchanged),
    ("CP7-T07", t07_lane_start_fills_model),
    ("CP7-T08", t08_lane_start_without_model_unchanged),
    ("CP7-T09", t09_model_empty_when_none_chosen),
    ("CP7-T10", t10_validator_lists_every_placeholder),
    ("CP7-T11", t11_badly_written_orchestrator_model_refused),
    ("CP7-T12", t12_choosing_a_model_does_not_approve_paid_use),
    ("CP7-T13", t13_orchestrator_model_changes_no_reviewer_rules),
    ("CP7-T14", t14_project_choice_overrides_global),
    ("CP7-T15", t15_readme_lists_the_new_file),
    ("CP7-T16", t16_inventory_matches_what_the_file_reads),
    ("CP7-T17", t17_full_suite_gate),
    ("CP7-T18", t18_unrunnable_model_paid_and_empty_model_notes),
    ("CP7-X01", tx01_session_model_not_offered_by_provider),
    ("CP7-X02", tx02_unknown_company_is_refused_where_work_starts),
    ("CP7-X03", tx03_custom_command_provider_gets_no_built_in_start_command),
    ("CP7-X04", tx04_inherit_means_no_model_argument),
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
