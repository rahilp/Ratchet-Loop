#!/usr/bin/env python3
"""
CP3 tests: discovery also finds Oh My Pi (omp), labels and picks its signed-in models the same way as
OpenCode, and lists Pi as a tool that needs a model.   python3 tests/test_discover_omp.py

A fake `omp` executable on a private PATH answers `omp models --json` with a fixed JSON catalog (the shape
app-scout recorded from the real CLI: {"models": [{"provider": ..., "id": ..., "selector": "<provider>/<id>"}
, ...]}) and logs every call (listing or probe/run) to a shared log file, so a case can prove a per-use or
unknown-cost model was never sent a request. A fake `pi` (no model-listing support) and a fake `crush` (no
preset at all, per app-scout.json: its flags could not be checked on this machine) are added where a case
needs them, each logging every call so a case can prove it was never invoked. tests/test_discover.py's fake
`opencode` (td.OPENCODE_SH) is reused where a case needs an already-working other-tool provider on the same
PATH. No real CLI or network is ever reached.

Each case is its own function in TESTS, run from main() inside a try/except so a crash is recorded as a
failed case, never a crash or a skip. Writes a per-test JSON report to RATCHET_TEST_REPORT when set:
{"cases": [{"id": "CP3-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, rx, ENV, approve_plan, say)
import test_discover as td      # noqa: E402  (OPENCODE_SH, discover, config_providers, log_text)

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


def load_engine():
    """A fresh load of ratchet.py as its own module, the way tests/test_status_plain.py does, so a case can
    inspect PRESETS / call provider_command without relying on the module already being on sys.modules."""
    spec = importlib.util.spec_from_file_location("ratchet_cp3_omp", ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------------------------------------ fixtures

# The fake omp CLI. `omp models --json` (exactly those two args) prints the fixed JSON catalog from
# $RATCHET_OMP_MODELS and logs "LIST models"; anything else is a probe/run call, logged in full as
# "PROBE <args>", then it fails (a fixed reason on stderr, exit 1) if $RATCHET_OMP_FAIL is set, otherwise it
# answers a connectivity-check prompt with RATCHET-PROBE-OK (the same probe token every other fake CLI in
# this repo answers) or a no-findings review. $RATCHET_OMP_LIST_FAIL makes the listing call itself fail (not
# installed / not signed in); $RATCHET_OMP_BAD_JSON makes it exit 0 but print something that isn't the
# catalog shape at all.
OMP_SH = r"""#!/bin/sh
log="$RATCHET_OMP_LOG"
if [ "$1" = "models" ] && [ "$2" = "--json" ] && [ "$#" -eq 2 ]; then
  [ -n "$log" ] && printf 'LIST models\n' >> "$log"
  if [ -n "$RATCHET_OMP_LIST_FAIL" ]; then
    echo "error: omp: not signed in to any provider" >&2
    exit 1
  fi
  if [ -n "$RATCHET_OMP_BAD_JSON" ]; then
    echo "omp: unexpected catalog response, not the JSON you were expecting"
    exit 0
  fi
  [ -n "$RATCHET_OMP_MODELS" ] && cat "$RATCHET_OMP_MODELS"
  exit 0
fi
[ -n "$log" ] && printf 'PROBE %s\n' "$*" >> "$log"
if [ -n "$RATCHET_OMP_FAIL" ]; then
  echo "error: omp request failed: rate limited" >&2
  exit 1
fi
case "$*" in
  *"connectivity check"*) echo RATCHET-PROBE-OK;;
  *) echo '{"findings": []}';;
esac
"""

# A plain executable with no model-listing support: any call (including "models") just logs itself and
# prints its own usage text, standing in for a real non-interactive CLI whose provider is unknown until
# signed in (app-scout.json's Pi fixture).
PI_SH = r"""#!/bin/sh
log="$RATCHET_PI_LOG"
[ -n "$log" ] && printf 'CALLED %s\n' "$*" >> "$log"
echo "pi: use -p to run a prompt"
"""

# Present on PATH but with no preset entry at all (its flags could not be checked on this machine, per
# app-scout.json), so it must never be invoked by discovery. Logs itself so a case can prove that.
CRUSH_SH = r"""#!/bin/sh
log="$RATCHET_CRUSH_LOG"
[ -n "$log" ] && printf 'CALLED %s\n' "$*" >> "$log"
echo "crush: unknown command"
"""

# CP3.json's own T01 fixture, verbatim: openai-codex (openai, via the developer's own signed-in Codex
# account) and three opencode-go models (zhipu/deepseek/google-flash) all reachable, signed in, through omp.
MODELS_T01 = {"models": [
    {"provider": "openai-codex", "id": "gpt-5.2", "selector": "openai-codex/gpt-5.2"},
    {"provider": "opencode-go", "id": "glm-5.3", "selector": "opencode-go/glm-5.3"},
    {"provider": "opencode-go", "id": "deepseek-v4-pro", "selector": "opencode-go/deepseek-v4-pro"},
    {"provider": "opencode-go", "id": "gemini-3.7-flash", "selector": "opencode-go/gemini-3.7-flash"},
]}


def fake_bin(include_opencode=False, include_pi=False, include_crush=False):
    """A private PATH with a fake `omp` (always) plus the real binaries the engine itself needs, and
    optionally a fake `opencode` / `pi` / `crush`. Returns a dict of paths this case's env needs. Never a
    real agent CLI."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-fakebin-omp-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    (b / "omp").write_text(OMP_SH)
    (b / "omp").chmod(0o755)
    paths = {
        "bin": b,
        "omp_log": Path(_tmp.mkdtemp(prefix="ratchet-omp-log-")) / "calls.log",
        "omp_models_file": Path(_tmp.mkdtemp(prefix="ratchet-omp-models-")) / "models.json",
    }
    if include_opencode:
        (b / "opencode").write_text(td.OPENCODE_SH)
        (b / "opencode").chmod(0o755)
        paths["oc_log"] = Path(_tmp.mkdtemp(prefix="ratchet-oc-log-")) / "calls.log"
        paths["oc_models_file"] = Path(_tmp.mkdtemp(prefix="ratchet-oc-models-")) / "models.txt"
        paths["oc_models_file"].write_text("opencode-go/glm-5.3\n")
    if include_pi:
        (b / "pi").write_text(PI_SH)
        (b / "pi").chmod(0o755)
        paths["pi_log"] = Path(_tmp.mkdtemp(prefix="ratchet-pi-log-")) / "calls.log"
    if include_crush:
        (b / "crush").write_text(CRUSH_SH)
        (b / "crush").chmod(0o755)
        paths["crush_log"] = Path(_tmp.mkdtemp(prefix="ratchet-crush-log-")) / "calls.log"
    return paths


def setup(name, models_json, host="anthropic", include_opencode=False, include_pi=False, include_crush=False,
          omp_list_fail=False, omp_bad_json=False, cps=1):
    """A fresh throwaway project plus the fake-omp (and optionally fake-opencode/pi/crush) env for it:
    (project dir, env dict, paths dict with every log/file this case built)."""
    paths = fake_bin(include_opencode=include_opencode, include_pi=include_pi, include_crush=include_crush)
    paths["omp_models_file"].write_text(json.dumps(models_json) if models_json is not None else "")
    env = dict(g.ENV)
    env["PATH"] = str(paths["bin"])
    env["RATCHET_HOST_PROVIDER"] = host
    env["RATCHET_OMP_LOG"] = str(paths["omp_log"])
    env["RATCHET_OMP_MODELS"] = str(paths["omp_models_file"])
    if omp_list_fail:
        env["RATCHET_OMP_LIST_FAIL"] = "1"
    if omp_bad_json:
        env["RATCHET_OMP_BAD_JSON"] = "1"
    if include_opencode:
        env["RATCHET_OPENCODE_LOG"] = str(paths["oc_log"])
        env["RATCHET_OPENCODE_MODELS"] = str(paths["oc_models_file"])
    if include_pi:
        env["RATCHET_PI_LOG"] = str(paths["pi_log"])
    if include_crush:
        env["RATCHET_CRUSH_LOG"] = str(paths["crush_log"])
    d = g.project(name, cps=cps)
    return d, env, paths


def find_added_omp_provider(d):
    """The one provider (if any) config.json gained with preset omp, after discovery ran."""
    return [p for p in td.config_providers(d) if p.get("preset") == "omp"]


def file_text(p) -> str:
    return p.read_text() if p.exists() else ""


# ------------------------------------------------------------------------------------------------ cases

def t01_adds_flat_rate_model_from_omp():
    d, env, paths = setup("cp3t01", MODELS_T01)
    r, out = td.discover(d, env)
    check("[CP3-T01] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_omp_provider(d)
    check("[CP3-T01] exactly one omp-based provider is added", len(added) == 1, added)
    model = added[0].get("model") if added else None
    check("[CP3-T01] the added provider's model is opencode-go/glm-5.3 (zhipu, the preferred family)",
          model == "opencode-go/glm-5.3", model)
    check("[CP3-T01] the added provider is recorded as flat-rate", added and added[0].get("cost") == "flat",
          added)
    log = file_text(paths["omp_log"])
    check("[CP3-T01] the log shows exactly one listing call", log.count("LIST models") == 1, log)
    probes = [ln for ln in log.splitlines() if "PROBE" in ln]
    check("[CP3-T01] the log shows exactly one probe/run call, for the picked model only",
          len(probes) == 1 and "opencode-go/glm-5.3" in probes[0], log)
    check("[CP3-T01] the log never names any other candidate (gpt-5.2, deepseek-v4-pro, gemini-3.7-flash)",
          "gpt-5.2" not in log and "deepseek-v4-pro" not in log and "gemini-3.7-flash" not in log, log)


def t02_prefers_company_with_no_reviewer_yet():
    d, env, paths = setup(
        "cp3t02",
        {"models": [{"provider": "openai-codex", "id": "gpt-5.2", "selector": "openai-codex/gpt-5.2"},
                    {"provider": "opencode-go", "id": "glm-5.3", "selector": "opencode-go/glm-5.3"}]},
        include_opencode=True)
    g.rx(d, "config", "--set",
         'providers=[{"id":"opencode-glm","preset":"opencode","model":"opencode-go/glm-5.3","cost":"flat"}]',
         env=env)
    r, out = td.discover(d, env)
    check("[CP3-T02] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_omp_provider(d)
    check("[CP3-T02] exactly one omp-based provider is added", len(added) == 1, added)
    model = added[0].get("model") if added else None
    check("[CP3-T02] the added model is openai-codex/gpt-5.2 (openai), not the already-covered zhipu glm",
          model == "openai-codex/gpt-5.2", model)
    omp_log = file_text(paths["omp_log"])
    check("[CP3-T02] omp's call log never shows a request naming the zhipu model (glm-5.3)",
          "glm-5.3" not in omp_log, omp_log)
    check("[CP3-T02] omp's call log shows one probe/run call naming the picked openai model",
          "gpt-5.2" in omp_log and "PROBE" in omp_log, omp_log)
    check("[CP3-T02] the already-registered opencode-glm provider is still present too",
          any(p.get("id") == "opencode-glm" for p in td.config_providers(d)), td.config_providers(d))


def t03_skipped_once_a_provider_from_it_is_registered():
    d, env, paths = setup("cp3t03", MODELS_T01)
    g.rx(d, "config", "--set",
         'providers=[{"id":"omp-codex","preset":"omp","model":"openai-codex/gpt-5.2","cost":"flat"}]',
         env=env)
    before = td.config_providers(d)
    r, out = td.discover(d, env)
    check("[CP3-T03] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    log = file_text(paths["omp_log"])
    check("[CP3-T03] omp's call log shows no listing call for this discovery pass",
          "LIST models" not in log, log)
    after = td.config_providers(d)
    check("[CP3-T03] no second omp-based provider is added",
          len([p for p in after if p.get("preset") == "omp"]) == 1, after)
    check("[CP3-T03] the already-registered provider is unchanged", after == before, (before, after))
    row = next((r2 for r2 in (out.get("providers") or []) if r2.get("id") == "omp-codex"), None)
    check("[CP3-T03] the existing omp provider is recognised as installed (the omp preset now resolves a "
          "real binary) and working",
          bool(row) and row.get("installed") is True and row.get("ok") is True, row)


def t04_paid_and_unknown_cost_models_never_probed():
    d, env, paths = setup(
        "cp3t04",
        {"models": [{"provider": "opencode-go", "id": "glm-5.3", "selector": "opencode-go/glm-5.3"},
                    {"provider": "opencode", "id": "gpt-5.1-codex", "selector": "opencode/gpt-5.1-codex"},
                    {"provider": "mystery-vendor", "id": "some-model",
                     "selector": "mystery-vendor/some-model"}]})
    r, out = td.discover(d, env)
    check("[CP3-T04] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    paid = out.get("paid_options") or []
    zen = next((p for p in paid if "gpt-5.1-codex" in json.dumps(p)), None)
    mystery = next((p for p in paid if "some-model" in json.dumps(p)), None)
    check("[CP3-T04] exactly two paid options are found (Zen-via-omp, unlisted-provider-via-omp)",
          len(paid) == 2, paid)
    check("[CP3-T04] the Zen-via-omp entry names omp as the tool and is labelled 'costs money per use'",
          bool(zen) and zen.get("tool") == "omp" and "costs money per use" in json.dumps(zen).lower(), zen)
    check("[CP3-T04] the unlisted-provider entry names omp as the tool and is labelled 'cost unknown, "
          "treated as paid'",
          bool(mystery) and mystery.get("tool") == "omp"
          and "cost unknown, treated as paid" in json.dumps(mystery).lower(), mystery)
    log = file_text(paths["omp_log"])
    check("[CP3-T04] the log has exactly one listing call and exactly one probe/run call (the flat pick)",
          log.count("LIST models") == 1 and len([ln for ln in log.splitlines() if "PROBE" in ln]) == 1, log)
    check("[CP3-T04] the log never names the per-use Zen model (gpt-5.1-codex)",
          "gpt-5.1-codex" not in log, log)
    check("[CP3-T04] the log never names the unknown-cost model (some-model)", "some-model" not in log, log)


def t05_paid_omp_model_goes_through_the_same_ask_flow():
    d, env, paths = setup("cp3t05", {"models": []})
    g.approve_plan(d)
    model = "opencode/gpt-5.1-codex"
    before_r = g.rx(d, "providers", "--add", "omp-or", "--preset", "omp", "--model", model, env=env)
    check("[CP3-T05] the --add before any reply is refused", before_r.returncode != 0,
          before_r.stdout + before_r.stderr)
    check("[CP3-T05] omp's call log is still empty: no test request was sent before the reply",
          file_text(paths["omp_log"]) == "", file_text(paths["omp_log"]))
    ask = g.rx(d, "ask", "paid-provider", "omp", model, env=env)
    text = ask.stdout + ask.stderr
    check("[CP3-T05] the question names omp and the exact model", "omp" in text and model in text, text)
    m = re.search(r'"([^"]*' + re.escape(model) + r'[^"]*)"', ask.stdout)
    exact = m.group(1) if m else f"pay per use: {model}"
    g.say(d, exact)
    after_r = g.rx(d, "providers", "--add", "omp-or", "--preset", "omp", "--model", model, env=env)
    check("[CP3-T05] the add after the exact reply succeeds", after_r.returncode == 0,
          after_r.stdout + after_r.stderr)
    log = file_text(paths["omp_log"])
    probes = [ln for ln in log.splitlines() if "PROBE" in ln and model.split("/", 1)[1] in ln]
    check("[CP3-T05] exactly one test request reached omp for that model", len(probes) == 1, log)
    added = [p for p in td.config_providers(d) if p.get("id") == "omp-or"]
    check("[CP3-T05] config.json records the provider with cost per-use",
          len(added) == 1 and added[0].get("cost") == "per-use", added)


def t06_review_command_is_noninteractive_and_keeps_no_session():
    eng = load_engine()
    pv = {"preset": "omp", "model": "opencode-go/glm-5.3"}
    cmd, stdin = eng.provider_command(pv)
    check("[CP3-T06] the command includes omp's print flag", "-p" in cmd or "--print" in cmd, cmd)
    check("[CP3-T06] the command includes the no-session flag", "--no-session" in cmd, cmd)
    check("[CP3-T06] the command includes --model followed by the chosen model",
          "--model" in cmd and cmd[cmd.index("--model") + 1] == "opencode-go/glm-5.3" if "--model" in cmd
          else False, cmd)
    persisting = {"--session", "--resume", "--continue", "--save-session"}
    check("[CP3-T06] the command includes no flag that persists or resumes a session",
          not (persisting & set(cmd)), cmd)


def t07_falls_back_to_needs_model_when_listing_cant_be_read():
    d, env, paths = setup("cp3t07a", MODELS_T01, include_pi=True, omp_list_fail=True)
    r, out = td.discover(d, env)
    check("[CP3-T07] providers --discover --json still succeeds when omp's listing exits non-zero",
          r.returncode == 0, r.stdout + r.stderr)
    check("[CP3-T07] nothing is added for omp (non-zero listing exit)", find_added_omp_provider(d) == [], "")
    check("[CP3-T07] omp appears in needs_model like a lister-less tool would",
          "omp" in (out.get("needs_model") or []), out)
    check("[CP3-T07] every other preset in the same run is still processed (pi still appears)",
          "pi" in (out.get("needs_model") or []), out)

    d2, env2, paths2 = setup("cp3t07b", MODELS_T01, include_pi=True, omp_bad_json=True)
    r2, out2 = td.discover(d2, env2)
    check("[CP3-T07] providers --discover --json still succeeds when omp prints unreadable output",
          r2.returncode == 0, r2.stdout + r2.stderr)
    check("[CP3-T07] nothing is added for omp (unreadable listing output)", find_added_omp_provider(d2) == [],
          "")
    check("[CP3-T07] omp appears in needs_model for the unreadable-output case too",
          "omp" in (out2.get("needs_model") or []), out2)
    check("[CP3-T07] pi is still processed in the unreadable-output case too",
          "pi" in (out2.get("needs_model") or []), out2)


def t08_pi_under_needs_model_crush_gets_no_preset():
    d, env, paths = setup("cp3t08", {"models": []}, include_pi=True, include_crush=True)
    r, out = td.discover(d, env)
    check("[CP3-T08] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    check("[CP3-T08] pi appears in needs_model so the agent can add it with a model",
          "pi" in (out.get("needs_model") or []), out)
    check("[CP3-T08] crush never appears anywhere in the output", "crush" not in json.dumps(out), out)
    check("[CP3-T08] crush is never invoked (its own call log stays empty)",
          file_text(paths["crush_log"]) == "", file_text(paths["crush_log"]))


def t09_docs_list_the_new_presets():
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    readme = (ROOT / "README.md").read_text()
    schemas = (ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md").read_text()
    check("[CP3-T09] schemas.md's provider/preset reference mentions Oh My Pi (omp)",
          re.search(r"Oh My Pi", schemas, re.I) is not None and re.search(r"\bomp\b", schemas) is not None, "")
    check("[CP3-T09] schemas.md's provider/preset reference mentions Pi", re.search(r"\bpi\b", schemas) is not None,
          "")
    check("[CP3-T09] README.md mentions Oh My Pi (omp)",
          re.search(r"Oh My Pi", readme, re.I) is not None and re.search(r"\bomp\b", readme) is not None, "")
    check("[CP3-T09] README.md mentions Pi", re.search(r"\bpi\b", readme) is not None, "")
    check("[CP3-T09] README.md says omp is discovered and labelled the same way as OpenCode",
          re.search(r"omp.{0,300}(?:same way|like OpenCode|as OpenCode)", readme, re.I | re.S) is not None
          or re.search(r"(?:same way|like OpenCode).{0,300}omp", readme, re.I | re.S) is not None, "")
    check("[CP3-T09] SKILL.md's step 3 exists and mentions omp",
          re.search(r"\n3\.\s+\*\*Check who else can review.*?omp", skill, re.I | re.S) is not None, skill[:200])


TESTS = [
    ("CP3-T01", t01_adds_flat_rate_model_from_omp),
    ("CP3-T02", t02_prefers_company_with_no_reviewer_yet),
    ("CP3-T03", t03_skipped_once_a_provider_from_it_is_registered),
    ("CP3-T04", t04_paid_and_unknown_cost_models_never_probed),
    ("CP3-T05", t05_paid_omp_model_goes_through_the_same_ask_flow),
    ("CP3-T06", t06_review_command_is_noninteractive_and_keeps_no_session),
    ("CP3-T07", t07_falls_back_to_needs_model_when_listing_cant_be_read),
    ("CP3-T08", t08_pi_under_needs_model_crush_gets_no_preset),
    ("CP3-T09", t09_docs_list_the_new_presets),
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
