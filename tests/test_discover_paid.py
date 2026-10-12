#!/usr/bin/env python3
"""
CP2 tests: Ratchet never spends the developer's money on a per-use (or unknown-cost) provider without an
exact yes from them first.   python3 tests/test_discover_paid.py

Reuses tests/test_gates.py's project()/approve_plan()/say() (an approved, active run owned by chat session
"s-owner") and tests/test_discover.py's fake `opencode` CLI (fake_opencode_bin/OPENCODE_SH), which logs every
`opencode models` listing call and every probe/run call to a shared log file, so a case can prove no request
ever reached a per-use or unknown-cost model. No real CLI or network is ever reached.

Models used (namespace before the first "/" decides cost via PROVIDER_COST):
  openrouter/z-ai/glm-5           OpenRouter, per-use   (the model the paid-provider question is asked about)
  openrouter/moonshotai/kimi-k3   OpenRouter, per-use, a DIFFERENT model/company (an OK for glm-5 must not cover it)
  opencode/glm-5.3                OpenCode Zen, per-use (CP1-T08's Zen fixture)
  mystery-vendor/glm-5.3          unlisted provider, cost unknown (zhipu is still recognised as the company)
  opencode-go/glm-5.3             OpenCode Go, flat-rate (needs no question at all)

Each case is its own function in TESTS, run from main() inside a try/except so a crash is recorded as a failed
case, never a crash or a skip. Writes a per-test JSON report to RATCHET_TEST_REPORT when set:
{"cases": [{"id": "CP2-T04", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, say, rx, ENV, git)
import test_discover as td      # noqa: E402  (fake_opencode_bin, log_text, config_providers)

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
_CUR_CASE: str | None = None


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    if _CUR_CASE:
        CASE_STATUS[_CUR_CASE] = CASE_STATUS.get(_CUR_CASE, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(_CUR_CASE, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ fixtures

MODEL_OR = "openrouter/z-ai/glm-5"               # OpenRouter, per-use: the model asked about
MODEL_OR2 = "openrouter/moonshotai/kimi-k3"       # a different OpenRouter model/company
MODEL_ZEN = "opencode/glm-5.3"                    # OpenCode Zen, per-use
MODEL_UNKNOWN = "mystery-vendor/glm-5.3"          # unlisted provider, cost unknown (zhipu recognised by name)
MODEL_FLAT = "opencode-go/glm-5.3"                # OpenCode Go, flat-rate

APPROVAL_WORDS = ("go ahead", "approved")  # the generic listed-yes words (APPROVAL_REPLIES minus bare "yes")


def refused(r):
    """A real refusal from the command, not an argparse usage error for an unknown command/choice."""
    return r.returncode != 0 and "usage:" not in r.stderr and "invalid choice" not in r.stderr


def setup(name, host="anthropic"):
    """A fresh, approved, active run (owned by chat session "s-owner") plus the fake-opencode env for it:
    (project dir, env dict with a private PATH, log file path)."""
    b, models_file = td.fake_opencode_bin(["opencode-go/glm-5.3"])
    log_path = Path(_tmp.mkdtemp(prefix="ratchet-paid-log-")) / "calls.log"
    env = dict(g.ENV)
    env["PATH"] = str(b)
    env["RATCHET_HOST_PROVIDER"] = host
    env["RATCHET_OPENCODE_MODELS"] = str(models_file)
    env["RATCHET_OPENCODE_LOG"] = str(log_path)
    d = g.project(name)
    g.approve_plan(d)
    return d, env, log_path


def add_cmd(d, env, id_, model, preset="opencode", no_check=False, extra=()):
    args = ["providers", "--add", id_, "--preset", preset, "--model", model]
    if no_check:
        args.append("--no-check")
    args += list(extra)
    return g.rx(d, *args, env=env)


def ask_paid(d, env, tool="opencode", model=MODEL_OR):
    return g.rx(d, "ask", "paid-provider", tool, model, env=env)


def reply(d, text, session="s-owner"):
    return g.say(d, text, session=session)


def state(d, slug="f") -> dict:
    p = Path(d) / f".ratchet/runs/{slug}/state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def pending(d):
    return (state(d) or {}).get("pending")


def extract_exact_reply(stdout, model):
    """Pull the exact reply text out of the printed question, the way an agent would read it, instead of
    guessing the wording. Falls back to the notes' suggested format only when nothing is printed yet."""
    for pat in (r'reply exactly "([^"]+)"', r'reply[^".\n]*"([^"]+)"', r'"([^"]*' + re.escape(model) + r'[^"]*)"'):
        m = re.search(pat, stdout, re.I)
        if m:
            return m.group(1)
    return f"pay per use: {model}"


# --------------------------------------------------------------------------- blocking fake CLI (CP2-X01 fix)

# Like test_discover.OPENCODE_SH, but every probe/run call (never `models`) blocks after logging PROBE until
# a sentinel file ($RATCHET_OPENCODE_SENTINEL) exists, so a test can hold the fake CLI mid-probe (state.lock
# still held) to make a lock-race regression test deterministic instead of relying on scheduling luck.
BLOCKING_OPENCODE_SH = r"""#!/bin/sh
log="$RATCHET_OPENCODE_LOG"
if [ "$#" -eq 1 ] && [ "$1" = "models" ]; then
  [ -n "$log" ] && printf 'LIST models\n' >> "$log"
  [ -n "$RATCHET_OPENCODE_MODELS" ] && cat "$RATCHET_OPENCODE_MODELS"
  exit 0
fi
[ -n "$log" ] && printf 'PROBE %s\n' "$*" >> "$log"
sentinel="$RATCHET_OPENCODE_SENTINEL"
if [ -n "$sentinel" ]; then
  while [ ! -f "$sentinel" ]; do sleep 0.05; done
fi
case "$*" in
  *"connectivity check"*) echo RATCHET-PROBE-OK;;
  *) echo '{"findings": []}';;
esac
"""


def fake_opencode_bin_blocking(models):
    """A private PATH with the blocking fake `opencode` above, built the same way as
    test_discover.fake_opencode_bin: (bin dir, models-list file)."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-fakebin-oc-block-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    (b / "opencode").write_text(BLOCKING_OPENCODE_SH)
    (b / "opencode").chmod(0o755)
    models_file = Path(_tmp.mkdtemp(prefix="ratchet-oc-models-block-")) / "models.txt"
    models_file.write_text("\n".join(models) + "\n")
    return b, models_file


def _wait_until(predicate, timeout=10.0, interval=0.05):
    """Poll predicate() until it returns something truthy or the timeout passes; returns that truthy value,
    or None on timeout. Used to synchronize on the fake CLI's own log instead of a fixed sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = predicate()
        if r:
            return r
        time.sleep(interval)
    return None


# ------------------------------------------------------------------------------------------------ cases

def t01_unasked_add_refused_no_call():
    d, env, log_path = setup("cp2t01")
    r = add_cmd(d, env, "opencode-glm", MODEL_OR)
    check("[CP2-T01] the add exits non-zero", r.returncode != 0, r.stdout + r.stderr)
    check("[CP2-T01] the message tells the agent to ask the paid-provider question first",
          re.search(r"ask", r.stderr + r.stdout, re.I) and re.search(r"pay|paid|per.use", r.stderr + r.stdout, re.I),
          r.stdout + r.stderr)
    check("[CP2-T01] nothing is added to config.json", td.config_providers(d) == [], td.config_providers(d))
    check("[CP2-T01] the fake opencode's call log is empty: no test request was sent",
          td.log_text(log_path) == "", td.log_text(log_path))


def t02_no_check_also_refused_before_add():
    d, env, log_path = setup("cp2t02")
    r = add_cmd(d, env, "opencode-glm", MODEL_OR, no_check=True)
    check("[CP2-T02] the add with --no-check exits non-zero", r.returncode != 0, r.stdout + r.stderr)
    check("[CP2-T02] config.json is unchanged", td.config_providers(d) == [], td.config_providers(d))
    check("[CP2-T02] nothing was probed (the call log is empty)",
          td.log_text(log_path) == "", td.log_text(log_path))


def t03_paid_question_names_tool_model_cost_and_exact_reply():
    d, env, log_path = setup("cp2t03")
    r = ask_paid(d, env, "opencode", MODEL_OR)
    text = r.stdout + r.stderr
    check("[CP2-T03] the question names the tool, opencode", "opencode" in text, text)
    check("[CP2-T03] the question names the exact model", MODEL_OR in text, text)
    check("[CP2-T03] the question says it costs money per use, including one small test request now",
          re.search(r"per.use", text, re.I) and re.search(r"test request", text, re.I), text)
    exact = extract_exact_reply(r.stdout, MODEL_OR)
    check("[CP2-T03] the question states one exact reply text the user must send",
          bool(exact) and exact in r.stdout, (exact, r.stdout))
    check("[CP2-T03] the generic listed-yes words are not offered here as an equivalent",
          not any(w in text.lower() for w in APPROVAL_WORDS), text)


def t04_exact_reply_then_add_sends_one_request_and_adds_per_use():
    d, env, log_path = setup("cp2t04")
    a = ask_paid(d, env, "opencode", MODEL_OR)
    exact = extract_exact_reply(a.stdout, MODEL_OR)
    reply(d, exact)
    r = add_cmd(d, env, "opencode-glm", MODEL_OR)
    check("[CP2-T04] the add succeeds", r.returncode == 0, r.stdout + r.stderr)
    log = td.log_text(log_path)
    probes = [ln for ln in log.splitlines() if "PROBE" in ln and MODEL_OR in ln]
    check("[CP2-T04] the fake opencode's call log shows exactly one request for the model",
          len(probes) == 1, log)
    added = [p for p in td.config_providers(d) if p.get("id") == "opencode-glm"]
    check("[CP2-T04] config.json records the provider", len(added) == 1, td.config_providers(d))
    check("[CP2-T04] it is recorded with cost per-use", added and added[0].get("cost") == "per-use", added)
    check("[CP2-T04] it carries a paid_ok_at timestamp", added and bool(added[0].get("paid_ok_at")), added)


def t05_any_other_reply_adds_nothing_and_closes_question():
    d, env, log_path = setup("cp2t05")
    ask_paid(d, env, "opencode", MODEL_OR)
    reply(d, "ok")
    r = add_cmd(d, env, "opencode-glm", MODEL_OR)
    check("[CP2-T05] no question is left pending",
          not (pending(d) or {}).get("action") == "paid-provider", pending(d))
    check("[CP2-T05] the add is still refused", r.returncode != 0, r.stdout + r.stderr)
    check("[CP2-T05] config.json is unchanged", td.config_providers(d) == [], td.config_providers(d))
    check("[CP2-T05] the fake CLI's call log is still empty", td.log_text(log_path) == "", td.log_text(log_path))


def t06_ok_for_one_model_does_not_cover_a_different_model():
    d, env, log_path = setup("cp2t06")
    a = ask_paid(d, env, "opencode", MODEL_OR)
    exact = extract_exact_reply(a.stdout, MODEL_OR)
    reply(d, exact)
    r = add_cmd(d, env, "opencode-other", MODEL_OR2)
    check("[CP2-T06] the add for the different model is still refused", r.returncode != 0, r.stdout + r.stderr)
    check("[CP2-T06] the different model is not added",
          not any(p.get("id") == "opencode-other" for p in td.config_providers(d)), td.config_providers(d))
    check("[CP2-T06] the call log shows no request for the different model",
          MODEL_OR2 not in td.log_text(log_path), td.log_text(log_path))


def t07_consumed_ok_does_not_cover_a_run_with_no_active_question():
    d, env, log_path = setup("cp2t07")
    a = ask_paid(d, env, "opencode", MODEL_OR)
    exact = extract_exact_reply(a.stdout, MODEL_OR)
    reply(d, exact)
    first = add_cmd(d, env, "opencode-glm", MODEL_OR)
    check("[CP2-T07] setup sanity: the first add (CP2-T04's flow) succeeds", first.returncode == 0,
          first.stdout + first.stderr)
    before = td.config_providers(d)
    before_log = td.log_text(log_path)
    second = add_cmd(d, env, "opencode-glm-2", MODEL_OR)
    check("[CP2-T07] a second add for the same model with no new pending question is refused",
          second.returncode != 0, second.stdout + second.stderr)
    check("[CP2-T07] config.json gained no second provider", td.config_providers(d) == before,
          td.config_providers(d))
    check("[CP2-T07] no second request was sent to the fake CLI",
          td.log_text(log_path) == before_log, (before_log, td.log_text(log_path)))


def t08_unknown_cost_model_needs_the_question_too():
    d, env, log_path = setup("cp2t08")
    r = add_cmd(d, env, "opencode-mystery", MODEL_UNKNOWN)
    check("[CP2-T08] the add for an unknown-cost model is refused, the same as a known per-use model",
          r.returncode != 0, r.stdout + r.stderr)
    check("[CP2-T08] the call log is empty: nothing was probed",
          td.log_text(log_path) == "", td.log_text(log_path))
    check("[CP2-T08] nothing is added to config.json", td.config_providers(d) == [], td.config_providers(d))


def t09_flat_rate_model_needs_no_question():
    d, env, log_path = setup("cp2t09")
    r = add_cmd(d, env, "opencode-flat", MODEL_FLAT)
    check("[CP2-T09] the add proceeds straight to the test request with no question asked",
          r.returncode == 0, r.stdout + r.stderr)
    log = td.log_text(log_path)
    check("[CP2-T09] the fake opencode's call log shows a probe/run call naming the flat-rate model",
          MODEL_FLAT in log and "PROBE" in log, log)
    added = [p for p in td.config_providers(d) if p.get("id") == "opencode-flat"]
    check("[CP2-T09] config.json records the flat-rate provider", len(added) == 1, td.config_providers(d))


def t10_docs_describe_the_paid_provider_question():
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    readme = (ROOT / "README.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    m = re.search(r"\n3\.\s+\*\*Check who else can review.*?(?=\n4\.\s+\*\*)", skill, re.S)
    step3 = m.group(0) if m else ""
    check("[CP2-T10] SKILL.md step 3 exists (setup: found the numbered step)", bool(step3), skill[:200])
    check("[CP2-T10] SKILL.md step 3 says to ask the developer, naming the exact reply, before a paid option is used",
          bool(step3) and re.search(r"ask.{0,300}exact reply", step3, re.I | re.S) is not None, step3)
    check("[CP2-T10] README.md describes asking before a per-use provider with an exact reply",
          re.search(r"ask.{0,400}exact reply", readme, re.I | re.S) is not None
          or re.search(r"exact reply.{0,400}ask", readme, re.I | re.S) is not None, "")
    check("[CP2-T10] schemas.md documents the provider entry's cost and paid_ok_at fields",
          re.search(r"paid_ok_at", schemas) is not None and re.search(r"cost", schemas) is not None, "")
    check("[CP2-T10] schemas.md documents the ask list's paid-provider action",
          re.search(r"paid-provider", schemas) is not None, "")


TESTS = [
    ("CP2-T01", t01_unasked_add_refused_no_call),
    ("CP2-T02", t02_no_check_also_refused_before_add),
    ("CP2-T03", t03_paid_question_names_tool_model_cost_and_exact_reply),
    ("CP2-T04", t04_exact_reply_then_add_sends_one_request_and_adds_per_use),
    ("CP2-T05", t05_any_other_reply_adds_nothing_and_closes_question),
    ("CP2-T06", t06_ok_for_one_model_does_not_cover_a_different_model),
    ("CP2-T07", t07_consumed_ok_does_not_cover_a_run_with_no_active_question),
    ("CP2-T08", t08_unknown_cost_model_needs_the_question_too),
    ("CP2-T09", t09_flat_rate_model_needs_no_question),
    ("CP2-T10", t10_docs_describe_the_paid_provider_question),
]


# ------------------------------------------------------------------------------------------ fix-round tests
# Added for adversarial round 1 findings S1/A1/A3 (correctness/standards: `providers --add` must run under
# the state lock so two concurrent adds against one answered paid-provider question can't both spend it),
# A2 (standards: a director-enrolled lane's chat reply must never pay for a provider) and A1 (correctness:
# the prompt hook must recognise the exact paid-provider reply and name the real follow-up command).

def x01_concurrent_adds_consume_one_ok_only_once():
    """Deterministic version (adversarial round 2 finding X1): the fake opencode blocks mid-probe on a
    sentinel file, so add #1 is guaranteed to still hold state.lock, inside its probe, when add #2 runs."""
    import subprocess as sp
    d, env, log_path = setup("cp2x01")
    sentinel = Path(_tmp.mkdtemp(prefix="ratchet-paid-sentinel-")) / "go"
    b, models_file = fake_opencode_bin_blocking(["opencode-go/glm-5.3"])
    env = dict(env, PATH=str(b), RATCHET_OPENCODE_MODELS=str(models_file),
               RATCHET_OPENCODE_SENTINEL=str(sentinel))
    a = ask_paid(d, env, "opencode", MODEL_OR)
    exact = extract_exact_reply(a.stdout, MODEL_OR)
    reply(d, exact)
    argv = [sys.executable, str(g.ENGINE), "providers", "--add", "opencode-glm", "--preset", "opencode",
            "--model", MODEL_OR]
    p1 = sp.Popen(argv, cwd=d, env=env, stdout=sp.PIPE, stderr=sp.PIPE, text=True)

    def probed():
        log = td.log_text(log_path)
        return "PROBE" in log and MODEL_OR in log

    got_probe = _wait_until(probed, timeout=10.0)
    check("[CP2-X01] add #1 reaches its probe (still holding state.lock) before add #2 starts",
          bool(got_probe), td.log_text(log_path))

    p2 = sp.run(argv, cwd=d, env=env, capture_output=True, text=True)
    check("[CP2-X01] add #2, started while add #1 holds the lock in its probe, is refused busy",
          refused(p2) and re.search(r"busy", p2.stdout + p2.stderr, re.I) is not None,
          p2.stdout + p2.stderr)
    log_mid = td.log_text(log_path)
    probes_mid = [ln for ln in log_mid.splitlines() if "PROBE" in ln and MODEL_OR in ln]
    check("[CP2-X01] add #2 never reached the fake CLI: still exactly one PROBE logged",
          len(probes_mid) == 1, log_mid)

    sentinel.write_text("go")  # release add #1
    out1 = p1.communicate(timeout=20)
    check("[CP2-X01] add #1, once released, succeeds", p1.returncode == 0, out1)

    log = td.log_text(log_path)
    probes = [ln for ln in log.splitlines() if "PROBE" in ln and MODEL_OR in ln]
    check("[CP2-X01] only one test request for the model ever reached the fake CLI",
          len(probes) == 1, log)
    added = [p for p in td.config_providers(d) if p.get("id") == "opencode-glm"]
    check("[CP2-X01] config.json ends with exactly one provider for this id, not a lost or doubled write",
          len(added) == 1, td.config_providers(d))

    third = add_cmd(d, env, "opencode-glm-3", MODEL_OR)
    check("[CP2-X01] a third add is refused: the one-time OK is already spent",
          refused(third), third.stdout + third.stderr)
    check("[CP2-X01] the third add adds no second provider for this model",
          not any(p.get("id") == "opencode-glm-3" for p in td.config_providers(d)), td.config_providers(d))


def x02_director_lane_refuses_paid_add_from_a_chat_reply():
    d, env, log_path = setup("cp2x02")
    # HOME isolated too (not just RATCHET_HOME): director_session_problem's "no hooks in use" shortcut reads the
    # real user-scope hook config at Path.home(), so without this a machine with Ratchet's own hooks installed
    # for the session running this test would see them as "installed" for this throwaway project too.
    fake_home = Path(_tmp.mkdtemp(prefix="ratchet-paid-home-"))
    denv = dict(env, RATCHET_ROLE="director", HOME=str(fake_home))
    rk = g.rx(d, "director-key", env=denv)
    en = g.rx(d, "director-enroll", env=denv)
    check("[CP2-X02] (setup) director-key and director-enroll succeed",
          rk.returncode == 0 and en.returncode == 0, (rk.stdout + rk.stderr, en.stdout + en.stderr))
    a = ask_paid(d, env, "opencode", MODEL_OR)
    exact = extract_exact_reply(a.stdout, MODEL_OR)
    reply(d, exact)  # a chat reply in this now director-enrolled lane
    r = add_cmd(d, env, "opencode-glm", MODEL_OR)
    check("[CP2-X02] the add is refused: a chat reply never pays for a provider in a director-enrolled lane",
          r.returncode != 0, r.stdout + r.stderr)
    check("[CP2-X02] the refusal names the director enrollment",
          bool(re.search(r"director", r.stdout + r.stderr, re.I)), r.stdout + r.stderr)
    check("[CP2-X02] nothing is added to config.json", td.config_providers(d) == [], td.config_providers(d))
    check("[CP2-X02] the fake CLI's call log is still empty: no test request was sent",
          td.log_text(log_path) == "", td.log_text(log_path))


def x03_hook_tells_the_agent_the_exact_reply_names_providers_add():
    d, env, log_path = setup("cp2x03")
    a = ask_paid(d, env, "opencode", MODEL_OR)
    exact = extract_exact_reply(a.stdout, MODEL_OR)
    r = reply(d, exact)
    out = r.stdout + r.stderr
    check("[CP2-X03] the hook says this is the exact reply", re.search(r"exact reply", out, re.I) is not None, out)
    check("[CP2-X03] the hook does NOT say it is withdrawn or approves nothing",
          "approves nothing" not in out.lower(), out)
    check("[CP2-X03] the hook names the real follow-up command, providers --add",
          "providers --add" in out, out)
    check("[CP2-X03] the hook never names the non-existent `ratchet.py paid-provider ...` command",
          not re.search(r"ratchet\.py paid-provider\b", out), out)


def x04_hook_tells_the_agent_a_plain_yes_is_not_the_exact_reply():
    d, env, log_path = setup("cp2x04")
    ask_paid(d, env, "opencode", MODEL_OR)
    r = reply(d, "yes")
    out = r.stdout + r.stderr
    check("[CP2-X04] the hook says a plain yes is NOT the exact reply",
          re.search(r"is NOT the exact reply", out, re.I) is not None, out)
    check("[CP2-X04] the hook says it approves nothing", "approves nothing" in out.lower(), out)
    check("[CP2-X04] the hook never sends the agent to the non-existent `ratchet.py paid-provider ...` command",
          not re.search(r"ratchet\.py paid-provider\b", out), out)


TESTS += [
    ("CP2-X01", x01_concurrent_adds_consume_one_ok_only_once),
    ("CP2-X02", x02_director_lane_refuses_paid_add_from_a_chat_reply),
    ("CP2-X03", x03_hook_tells_the_agent_the_exact_reply_names_providers_add),
    ("CP2-X04", x04_hook_tells_the_agent_a_plain_yes_is_not_the_exact_reply),
]


# ------------------------------------------------------------------------------------------ fix-round tests
# Added for adversarial round 2 finding B1 (doc-code drift: a behavior change to LONG_LOCKED_CMDS/providers
# --add must be mirrored into schemas.md section 8 in the same step).

def x05_schemas_names_every_long_locked_cmd():
    engine_src = (ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py").read_text()
    m = re.search(r'LONG_LOCKED_CMDS\s*=\s*\(([^)]*)\)', engine_src)
    check("[CP2-X05] (setup) LONG_LOCKED_CMDS is found in ratchet.py", bool(m), engine_src[:0])
    names = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    check("[CP2-X05] (setup) LONG_LOCKED_CMDS has at least one entry", bool(names), names)
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    missing = [n for n in names if f"`{n}" not in schemas]
    check("[CP2-X05] schemas.md section 8 names every LONG_LOCKED_CMDS entry from ratchet.py",
          not missing, (names, missing))


TESTS += [
    ("CP2-X05", x05_schemas_names_every_long_locked_cmd),
]


# ------------------------------------------------------------------------------------------ fix-round tests
# Added for adversarial round 3 finding C1 (doc-code drift: paid_options entries carry an `add_model` key
# that schemas.md's shape for `paid_options` doesn't mention).

def x06_schemas_names_every_paid_options_key():
    d, env, _log_path = td.setup("cp2x06")
    r, out = td.discover(d, env)
    check("[CP2-X06] (setup) providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    paid = out.get("paid_options") or []
    check("[CP2-X06] (setup) the output lists paid_options", bool(paid), out)
    keys = set(paid[0].keys()) if paid else set()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    missing = [k for k in keys if f"{k}" not in schemas]
    check("[CP2-X06] schemas.md names every key a paid_options entry carries",
          not missing, (sorted(keys), missing))


TESTS += [
    ("CP2-X06", x06_schemas_names_every_paid_options_key),
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
    global _CUR_CASE
    for cid, fn in TESTS:
        _CUR_CASE = cid
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a result for this case, never a silent drop
            global FAIL
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
    _CUR_CASE = None
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
