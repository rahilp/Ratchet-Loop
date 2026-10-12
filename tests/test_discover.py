#!/usr/bin/env python3
"""
CP1 tests: discovery adds a flat-rate OpenCode model by itself.   python3 tests/test_discover.py

A fake `opencode` executable on a private PATH answers `opencode models` with a fixed provider/model
list and logs every call it receives (including probe/run calls) to a shared log file. Every case runs
`ratchet.py providers --discover --json` against a fresh throwaway project (tests/test_gates.py's
`project()`) with the host set to anthropic (tests/test_gates.py's `ENV` already does this) and a
private PATH that contains nothing but the fake `opencode` and the handful of real binaries the engine
itself needs (python3, git, sh, ...), so no real CLI or network is ever reached and no other agent CLI
on this machine is picked up by `--discover`.

Fixed model list (provider/model), matching .ratchet/runs/.../tests/CP1.json's fixtures exactly:
  opencode-go/claude-sonnet-5   anthropic, flat   (same company as the host: never picked)
  opencode-go/foo-widget        company unknown, flat   (unmappable: never picked)
  opencode-go/glm-5.3-flash     zhipu, flat, small variant   (loses to its full sibling)
  opencode-go/glm-5.3           zhipu, flat   (the intended pick)
  opencode/gpt-5.1-codex        openai, OpenCode Zen, per-use   (never probed)
  openrouter/z-ai/glm-5         zhipu, OpenRouter, per-use   (never probed)
  mystery-vendor/some-model     unlisted provider, unknown cost   (never probed)

The fake CLI: `opencode models` (and nothing else as an argument) prints the fixed list and logs
"LIST models"; any other invocation is a probe/run call, logged in full ("PROBE <args>"), and answers
a connectivity-check prompt with RATCHET-PROBE-OK unless an `opencode.fail` marker file sits next to
the script, in which case it reports a fixed failure reason on stderr and exits 1.

Each case is its own function in TESTS, run from main() inside a try/except so a crash (an
AttributeError from code that doesn't exist yet, etc.) is recorded as a failed case, never a crash or a
skip. Writes a per-test JSON report to RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP1-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, rx, ENV, git)

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

FIXED_MODELS = [
    "opencode-go/claude-sonnet-5",
    "opencode-go/foo-widget",
    "opencode-go/glm-5.3-flash",
    "opencode-go/glm-5.3",
    "opencode/gpt-5.1-codex",
    "openrouter/z-ai/glm-5",
    "mystery-vendor/some-model",
]

# The fake opencode CLI. `opencode models` (no other argument) lists the fixed set and logs "LIST models";
# anything else is a probe/run call: logged in full as "PROBE <args>", then it fails with a fixed reason if
# an `opencode.fail` marker sits next to the script, otherwise it answers a connectivity-check prompt with
# RATCHET-PROBE-OK (the same probe token every other fake CLI in this repo answers) or a no-findings review.
OPENCODE_SH = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
log="$RATCHET_OPENCODE_LOG"
if [ "$#" -eq 1 ] && [ "$1" = "models" ]; then
  [ -n "$log" ] && printf 'LIST models\n' >> "$log"
  [ -n "$RATCHET_OPENCODE_MODELS" ] && cat "$RATCHET_OPENCODE_MODELS"
  exit 0
fi
[ -n "$log" ] && printf 'PROBE %s\n' "$*" >> "$log"
if [ -f "$here/$me.fail" ]; then
  echo "error: opencode-go request failed: rate limited" >&2
  exit 1
fi
case "$*" in
  *"connectivity check"*) echo RATCHET-PROBE-OK;;
  *) echo '{"findings": []}';;
esac
"""


def fake_opencode_bin(models, fail=False):
    """A private PATH with a fake `opencode` (serving `models`) plus the real binaries the engine itself
    needs, and a models-list file the fake CLI reads via $RATCHET_OPENCODE_MODELS. Never a real agent CLI."""
    b = Path(_tmp.mkdtemp(prefix="ratchet-fakebin-oc-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    (b / "opencode").write_text(OPENCODE_SH)
    (b / "opencode").chmod(0o755)
    if fail:
        (b / "opencode.fail").write_text("x")
    models_file = Path(_tmp.mkdtemp(prefix="ratchet-oc-models-")) / "models.txt"
    models_file.write_text("\n".join(models) + "\n")
    return b, models_file


def setup(name, models=FIXED_MODELS, fail=False, host="anthropic", cps=1):
    """A fresh throwaway project plus the fake-opencode env for it: (project dir, env dict, log file path)."""
    b, models_file = fake_opencode_bin(models, fail=fail)
    log_path = Path(_tmp.mkdtemp(prefix="ratchet-oc-log-")) / "calls.log"
    env = dict(g.ENV)
    env["PATH"] = str(b)
    env["RATCHET_HOST_PROVIDER"] = host
    env["RATCHET_OPENCODE_MODELS"] = str(models_file)
    env["RATCHET_OPENCODE_LOG"] = str(log_path)
    d = g.project(name, cps=cps)
    return d, env, log_path


def discover(d, env):
    r = g.rx(d, "providers", "--discover", "--json", env=env)
    try:
        out = json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        out = {}
    return r, out


def config_providers(d):
    cfg = json.loads((d / ".ratchet/config.json").read_text())
    return cfg.get("providers") or []


def log_text(log_path) -> str:
    return log_path.read_text() if log_path.exists() else ""


def find_added_opencode_provider(d):
    """The one provider (if any) config.json gained with preset opencode, after discovery ran."""
    return [p for p in config_providers(d) if p.get("preset") == "opencode"]


# ------------------------------------------------------------------------------------------------ cases

def t01_adds_flat_rate_opencode_go_model():
    d, env, log_path = setup("cp1t01")
    r, out = discover(d, env)
    check("[CP1-T01] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    check("[CP1-T01] exactly one opencode-based provider is added", len(added) == 1, added)
    model = added[0].get("model") if added else None
    check("[CP1-T01] the added provider's model is opencode-go/glm-5.3, not any other candidate",
          model == "opencode-go/glm-5.3", model)
    check("[CP1-T01] the added provider is recorded as flat-rate", added and added[0].get("cost") == "flat",
          added)
    row = next((r2 for r2 in (out.get("providers") or []) if r2.get("model") == "opencode-go/glm-5.3"), None)
    check("[CP1-T01] the JSON row for it is marked ok (working)", bool(row) and row.get("ok") is True, row)
    check("[CP1-T01] the JSON row for it says it is flat-rate", bool(row) and "flat" in json.dumps(row).lower(),
          row)
    log = log_text(log_path)
    check("[CP1-T01] the fake CLI's log shows a probe/run call naming opencode-go/glm-5.3",
          log.count("opencode-go/glm-5.3") >= 1 and "PROBE" in log, log)


def t02_paid_and_unknown_cost_options_never_called():
    d, env, log_path = setup("cp1t02")
    r, out = discover(d, env)
    check("[CP1-T02] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    paid = out.get("paid_options") or []
    check("[CP1-T02] the output lists paid_options", bool(paid), out)
    zen = next((p for p in paid if "gpt-5.1-codex" in json.dumps(p)), None)
    openrouter = next((p for p in paid if "z-ai/glm-5" in json.dumps(p)), None)
    mystery = next((p for p in paid if "some-model" in json.dumps(p)), None)
    check("[CP1-T02] the OpenCode Zen model is labelled 'costs money per use'",
          bool(zen) and "costs money per use" in json.dumps(zen).lower(), zen)
    check("[CP1-T02] the OpenRouter model is labelled 'costs money per use'",
          bool(openrouter) and "costs money per use" in json.dumps(openrouter).lower(), openrouter)
    check("[CP1-T02] the unlisted-provider model is labelled 'cost unknown, treated as paid'",
          bool(mystery) and "cost unknown, treated as paid" in json.dumps(mystery).lower(), mystery)
    log = log_text(log_path)
    check("[CP1-T02] the call log has no probe/run call naming the Zen model (gpt-5.1-codex)",
          "gpt-5.1-codex" not in log, log)
    check("[CP1-T02] the call log has no probe/run call naming the OpenRouter model (z-ai/glm-5)",
          "z-ai/glm-5" not in log, log)
    check("[CP1-T02] the call log has no probe/run call naming the unlisted-provider model (some-model)",
          "some-model" not in log, log)


def t03_same_company_model_never_picked():
    d, env, log_path = setup("cp1t03", models=["opencode-go/claude-sonnet-5", "opencode-go/glm-5.3"])
    r, out = discover(d, env)
    check("[CP1-T03] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    model = added[0].get("model") if added else None
    check("[CP1-T03] the added provider's model is glm-5.3, never the host's own company's claude-sonnet-5",
          model == "opencode-go/glm-5.3", model)
    log = log_text(log_path)
    check("[CP1-T03] the call log shows no probe/run call naming claude-sonnet-5",
          "claude-sonnet-5" not in log, log)


def t04_unmappable_company_model_never_picked():
    d, env, log_path = setup("cp1t04", models=["opencode-go/foo-widget", "opencode-go/glm-5.3"])
    r, out = discover(d, env)
    check("[CP1-T04] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    model = added[0].get("model") if added else None
    check("[CP1-T04] the added provider's model is glm-5.3, never the unmappable foo-widget",
          model == "opencode-go/glm-5.3", model)
    log = log_text(log_path)
    check("[CP1-T04] the call log shows no probe/run call naming foo-widget", "foo-widget" not in log, log)


def t05_small_variant_loses_to_full_sibling():
    d, env, log_path = setup("cp1t05", models=["opencode-go/glm-5.3-flash", "opencode-go/glm-5.3"])
    r, out = discover(d, env)
    check("[CP1-T05] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    model = added[0].get("model") if added else None
    check("[CP1-T05] the added provider's model is glm-5.3, never the small/preview glm-5.3-flash",
          model == "opencode-go/glm-5.3", model)
    log = log_text(log_path)
    check("[CP1-T05] the call log shows no probe/run call naming glm-5.3-flash", "glm-5.3-flash" not in log, log)


def t06_failed_test_request_adds_nothing():
    d, env, log_path = setup("cp1t06", models=["opencode-go/glm-5.3"], fail=True)
    r, out = discover(d, env)
    check("[CP1-T06] providers --discover --json still succeeds (a failing candidate isn't a crash)",
          r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    check("[CP1-T06] no opencode-based provider is added", added == [], added)
    check("[CP1-T06] the output explains the test request failed, naming the fake CLI's own reason",
          "rate limited" in json.dumps(out).lower(), out)


def t07_already_registered_opencode_left_alone():
    d, env, log_path = setup("cp1t07")
    g.rx(d, "config", "--set",
         'providers=[{"id":"opencode-glm","preset":"opencode","model":"opencode-go/glm-5.3","cost":"flat"}]',
         env=env)
    before = config_providers(d)
    r, out = discover(d, env)
    check("[CP1-T07] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    after = config_providers(d)
    check("[CP1-T07] no second opencode-based provider is added",
          len([p for p in after if p.get("preset") == "opencode"]) == 1, after)
    check("[CP1-T07] the already-registered provider is unchanged",
          any(p == {"id": "opencode-glm", "preset": "opencode", "model": "opencode-go/glm-5.3", "cost": "flat"}
              for p in after),
          after)
    check("[CP1-T07] setup sanity: there was exactly one opencode provider registered before discovery too",
          len([p for p in before if p.get("preset") == "opencode"]) == 1, before)
    log = log_text(log_path)
    check("[CP1-T07] the call log shows no new `opencode models` listing call for this discovery pass",
          "LIST models" not in log, log)


def t08_paid_options_name_tool_provider_model_and_say_ask_first():
    d, env, log_path = setup("cp1t08")
    r, out = discover(d, env)
    check("[CP1-T08] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    paid = out.get("paid_options") or []
    check("[CP1-T08] the output lists paid_options", bool(paid), out)
    zen = next((p for p in paid if "gpt-5.1-codex" in json.dumps(p)), None)
    openrouter = next((p for p in paid if "z-ai/glm-5" in json.dumps(p)), None)
    mystery = next((p for p in paid if "some-model" in json.dumps(p)), None)
    check("[CP1-T08] the Zen entry names its tool (opencode) and its exact model (gpt-5.1-codex)",
          bool(zen) and zen.get("tool") == "opencode" and zen.get("model") == "gpt-5.1-codex", zen)
    check("[CP1-T08] the OpenRouter entry names its tool (opencode) and its exact model (z-ai/glm-5)",
          bool(openrouter) and openrouter.get("tool") == "opencode" and openrouter.get("model") == "z-ai/glm-5",
          openrouter)
    check("[CP1-T08] the unlisted-provider entry names its tool (opencode) and its exact model (some-model)",
          bool(mystery) and mystery.get("tool") == "opencode" and mystery.get("model") == "some-model", mystery)
    for label, entry in (("Zen", zen), ("OpenRouter", openrouter), ("unlisted-provider", mystery)):
        text = json.dumps(entry).lower()
        check(f"[CP1-T08] the {label} entry's text says the developer must be asked before it is used",
              bool(entry) and "ask" in text and "before" in text, entry)


def t09_docs_describe_opencode_discovery():
    skill = (ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md").read_text()
    readme = (ROOT / "README.md").read_text()
    m = re.search(r"\n3\.\s+\*\*Check who else can review.*?(?=\n4\.\s+\*\*)", skill, re.S)
    step3 = m.group(0) if m else ""
    check("[CP1-T09] SKILL.md Phase A step 3 exists (setup: found the numbered step to check)", bool(step3), skill[:200])
    check("[CP1-T09] SKILL.md step 3 says discovery itself picks a flat-rate OpenCode model",
          re.search(r"OpenCode.{0,300}flat.rate", step3, re.I | re.S) is not None
          or re.search(r"flat.rate.{0,300}OpenCode", step3, re.I | re.S) is not None, step3)
    check("[CP1-T09] SKILL.md step 3 says per-use/unknown-cost options are listed to ask the user about first",
          re.search(r"ask.{0,200}(?:per.use|costs money|unknown cost|paid)", step3, re.I | re.S) is not None
          or re.search(r"(?:per.use|costs money|unknown cost|paid).{0,200}ask", step3, re.I | re.S) is not None,
          step3)
    check("[CP1-T09] README.md says discovery itself picks a flat-rate OpenCode model",
          re.search(r"OpenCode.{0,400}flat.rate", readme, re.I | re.S) is not None
          or re.search(r"flat.rate.{0,400}OpenCode", readme, re.I | re.S) is not None, "")
    check("[CP1-T09] README.md says per-use/unknown-cost options are listed to ask the user about first",
          re.search(r"ask.{0,300}(?:per.use|costs money|unknown cost|paid)", readme, re.I | re.S) is not None
          or re.search(r"(?:per.use|costs money|unknown cost|paid).{0,300}ask", readme, re.I | re.S) is not None,
          "")


TESTS = [
    ("CP1-T01", t01_adds_flat_rate_opencode_go_model),
    ("CP1-T02", t02_paid_and_unknown_cost_options_never_called),
    ("CP1-T03", t03_same_company_model_never_picked),
    ("CP1-T04", t04_unmappable_company_model_never_picked),
    ("CP1-T05", t05_small_variant_loses_to_full_sibling),
    ("CP1-T06", t06_failed_test_request_adds_nothing),
    ("CP1-T07", t07_already_registered_opencode_left_alone),
    ("CP1-T08", t08_paid_options_name_tool_provider_model_and_say_ask_first),
    ("CP1-T09", t09_docs_describe_opencode_discovery),
]


# ------------------------------------------------------------------------------------------ fix-round tests
# Added for adversarial round 1 findings A1-A3 (correctness) and A3 (standards): paid_options must collapse
# many models under one provider to one representative per company, a multi-company pick must prefer GLM
# and Kimi first, a listing with no pickable candidate must still surface under needs_model, and a listing
# whose output has no 'provider/model'-shaped line must be treated as a failure, not garbage.

def x01_paid_options_collapse_many_models_to_one_per_provider_and_company():
    d, env, log_path = setup(
        "cp1x01",
        models=["opencode-go/glm-5.3", "openrouter/z-ai/glm-5-flash", "openrouter/z-ai/glm-5",
                "openrouter/openai/gpt-5.9"])
    r, out = discover(d, env)
    check("[CP1-X01] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    paid = out.get("paid_options") or []
    openrouter_zhipu = [p for p in paid if p.get("provider") == "openrouter" and "glm" in json.dumps(p)]
    check("[CP1-X01] the two OpenRouter GLM lines collapse to exactly one paid entry",
          len(openrouter_zhipu) == 1, paid)
    check("[CP1-X01] the collapsed entry names the full model, not the small/preview variant",
          bool(openrouter_zhipu) and openrouter_zhipu[0].get("model") == "z-ai/glm-5", openrouter_zhipu)
    openrouter_openai = [p for p in paid if p.get("provider") == "openrouter" and "gpt-5.9" in json.dumps(p)]
    check("[CP1-X01] a different company under the same provider still gets its own entry",
          len(openrouter_openai) == 1, paid)
    log = log_text(log_path)
    check("[CP1-X01] the call log shows no probe/run call naming any OpenRouter model",
          "z-ai/glm-5" not in log and "gpt-5.9" not in log, log)


def x02_preferred_family_beats_an_alphabetically_earlier_company():
    d, env, log_path = setup("cp1x02", models=["opencode-go/qwen3.8-max", "opencode-go/glm-5.3"])
    r, out = discover(d, env)
    check("[CP1-X02] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    model = added[0].get("model") if added else None
    check("[CP1-X02] GLM (a preferred family) is picked over alibaba's qwen, though qwen sorts first",
          model == "opencode-go/glm-5.3", model)
    log = log_text(log_path)
    check("[CP1-X02] the call log shows no probe/run call naming the un-preferred qwen model",
          "qwen3.8-max" not in log, log)


def x03_unpickable_listing_still_surfaces_under_needs_model():
    d, env, log_path = setup("cp1x03", models=["opencode-go/foo-widget"])
    r, out = discover(d, env)
    check("[CP1-X03] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    check("[CP1-X03] no opencode-based provider is added (nothing pickable)", added == [], added)
    check("[CP1-X03] opencode still appears under needs_model so it can be added by hand",
          "opencode" in (out.get("needs_model") or []), out)


def x04_unreadable_listing_falls_back_to_needs_model_not_a_crash():
    d, env, log_path = setup("cp1x04", models=["error: opencode-go request failed: rate limited"])
    r, out = discover(d, env)
    check("[CP1-X04] providers --discover --json succeeds even on unreadable listing output",
          r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    check("[CP1-X04] no opencode-based provider is added", added == [], added)
    check("[CP1-X04] opencode falls back to needs_model, like an empty listing",
          "opencode" in (out.get("needs_model") or []), out)
    paid = out.get("paid_options") or []
    check("[CP1-X04] the unreadable line never becomes a garbage paid_options entry",
          not any("rate limited" in json.dumps(p).lower() for p in paid), paid)


TESTS += [
    ("CP1-X01", x01_paid_options_collapse_many_models_to_one_per_provider_and_company),
    ("CP1-X02", x02_preferred_family_beats_an_alphabetically_earlier_company),
    ("CP1-X03", x03_unpickable_listing_still_surfaces_under_needs_model),
    ("CP1-X04", x04_unreadable_listing_falls_back_to_needs_model_not_a_crash),
]


# ------------------------------------------------------------------------------------------ fix-round 2 tests
# Added for adversarial round 2 findings X2 (version numbers must compare as integer tuples, not floats, so
# an x.10 release doesn't sort below x.9) and A1 (a small-variant name must be caught even with no separator
# before the next letter, like "flashx"; and in a namespace PROVIDER_COST already prices, an unrecognised
# company's model is skipped rather than offered as something to pay for).

def x05_version_tuple_x10_beats_x9():
    d, env, log_path = setup("cp1x05", models=["opencode-go/glm-5.9", "opencode-go/glm-5.10"])
    r, out = discover(d, env)
    check("[CP1-X05] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    model = added[0].get("model") if added else None
    check("[CP1-X05] the added provider's model is glm-5.10, the newer release, not glm-5.9",
          model == "opencode-go/glm-5.10", model)
    log = log_text(log_path)
    check("[CP1-X05] the call log shows no probe/run call naming glm-5.9", "glm-5.9\n" not in log and
          "glm-5.9 " not in log and not log.rstrip().endswith("glm-5.9"), log)


def x06_flashx_excluded_and_unmappable_company_skipped_in_known_namespace():
    d, env, log_path = setup(
        "cp1x06",
        models=["opencode-go/glm-5.3", "openrouter/z-ai/glm-5.3-flashx", "openrouter/z-ai/glm-5.3",
                "openrouter/undi95/remm-slerp-l2-13b", "mystery-vendor/some-model"])
    r, out = discover(d, env)
    check("[CP1-X06] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    paid = out.get("paid_options") or []
    zhipu = [p for p in paid if p.get("provider") == "openrouter" and "glm" in json.dumps(p)]
    check("[CP1-X06] the OpenRouter zhipu entry names the full model, not the flashx variant",
          len(zhipu) == 1 and zhipu[0].get("model") == "z-ai/glm-5.3", zhipu)
    unmappable = [p for p in paid if p.get("provider") == "openrouter" and "remm-slerp" in json.dumps(p)]
    check("[CP1-X06] the unrecognised-company OpenRouter model is not listed (OpenRouter is a known paid "
          "namespace)", unmappable == [], paid)
    mystery = [p for p in paid if "some-model" in json.dumps(p)]
    check("[CP1-X06] the unlisted-provider's unrecognised-company model is still listed as before",
          len(mystery) == 1 and mystery[0].get("model") == "some-model", paid)


TESTS += [
    ("CP1-X05", x05_version_tuple_x10_beats_x9),
    ("CP1-X06", x06_flashx_excluded_and_unmappable_company_skipped_in_known_namespace),
]


# ------------------------------------------------------------------------------------------ fix-round 3 tests
# Added for adversarial round 3 finding B1/A1 (re-raised across four reviewers): the capable-model ranking
# must pick each company's real flagship, never a dated, small, safety-classifier or media-only variant,
# and it must do this the same way for the flat auto-pick and every paid entry (one shared ranking, not two).
# REALISTIC_LISTING below is copied verbatim (one `provider/model` per line) from this machine's own
# `opencode models` output on 2026-10-11 (OpenCode 1.18.27, OpenCode Go + OpenRouter signed in): a real
# flat (opencode-go/) slice plus a real openrouter/ slice spanning nine companies, each with its real dated
# or small/preview/safety/media sibling alongside its real newest flagship, exactly the shape the finding
# named (mistral-small-2603 next to mistral-large-4-0, a 2025-dated qwen3 next to the newest qwen, a llama
# safety classifier next to the newest llama, an image-only gemini next to a real gemini pro, gpt-oss next
# to the newest gpt, a glm "flashx" variant next to the current glm).

REALISTIC_LISTING = [
    # flat (opencode-go/): host's own company excluded, zhipu (a preferred family) is the flat auto-pick
    "opencode-go/claude-haiku-5-5",
    "opencode-go/glm-5.3-flash",
    "opencode-go/glm-5.3",
    "opencode-go/qwen3.8-max",
    # paid (openrouter/): mistral - dated/small siblings next to the real flagship mistral-large-4-0
    "openrouter/mistralai/mistral-small-2603",
    "openrouter/mistralai/mistral-small-24b-instruct-2501",
    "openrouter/mistralai/mistral-medium-3",
    "openrouter/mistralai/mistral-large-2512",
    "openrouter/mistralai/mistral-large-4-0",
    # paid: alibaba/qwen - a 2025-dated qwen3 next to the real newest qwen3.8-max-0902
    "openrouter/qwen/qwen3-235b-a22b-thinking-2507",
    "openrouter/qwen/qwen3.7-max",
    "openrouter/qwen/qwen3.8-flash",
    "openrouter/qwen/qwen3.8-max-0902",
    # paid: meta/llama - a safety classifier next to the real newest llama-4-maverick
    "openrouter/meta-llama/llama-guard-4-12b",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "openrouter/meta-llama/llama-4-maverick",
    # paid: google/gemini - image-only and preview siblings next to the real gemini-2.5-pro
    "openrouter/google/gemini-3-pro-image",
    "openrouter/google/gemini-3.1-pro-preview",
    "openrouter/google/gemini-2.5-pro",
    # paid: openai - gpt-oss siblings next to the real newest gpt-6.1-sol
    "openrouter/openai/gpt-oss-120b",
    "openrouter/openai/gpt-oss-20b",
    "openrouter/openai/gpt-5.1-codex",
    "openrouter/openai/gpt-6.1-sol",
    # paid: zhipu via openrouter - a flashx sibling next to the real glm-5.3
    "openrouter/z-ai/glm-5.3-flashx",
    "openrouter/z-ai/glm-5.3",
    # paid: deepseek - a flash sibling next to the real deepseek-v4-pro
    "openrouter/deepseek/deepseek-v4-flash",
    "openrouter/deepseek/deepseek-v3.2",
    "openrouter/deepseek/deepseek-v4-pro",
    # paid: moonshot/kimi - an older release next to the real newest kimi-k3
    "openrouter/moonshotai/kimi-k2.6",
    "openrouter/moonshotai/kimi-k3",
    # paid: xai/grok - an older release next to the real newest grok-4.20
    "openrouter/x-ai/grok-4.7",
    "openrouter/x-ai/grok-4.20",
]

# Every paid group's expected flagship (exact "namespace/model" shown to the developer) and a handful of
# reject words that must never appear in any suggestion, flat or paid, however the model family spells its
# dated/small/safety/media sibling.
EXPECTED_PAID_FLAGSHIPS = {
    "mistralai/mistral-large-4-0", "qwen/qwen3.8-max-0902", "meta-llama/llama-4-maverick",
    "google/gemini-2.5-pro", "openai/gpt-6.1-sol", "z-ai/glm-5.3", "deepseek/deepseek-v4-pro",
    "moonshotai/kimi-k3", "x-ai/grok-4.20",
}
NEVER_PICK_WORDS = ("small", "guard", "image", "preview", "flash", "oss")


def x07_realistic_listing_always_picks_each_companys_flagship():
    d, env, log_path = setup("cp1x07", models=REALISTIC_LISTING)
    r, out = discover(d, env)
    check("[CP1-X07] providers --discover --json succeeds", r.returncode == 0, r.stdout + r.stderr)
    added = find_added_opencode_provider(d)
    model = added[0].get("model") if added else None
    check("[CP1-X07] the flat pick is zhipu's real flagship, glm-5.3, never a dated/small/guard/image "
          "variant from any company", model == "opencode-go/glm-5.3", model)
    paid = out.get("paid_options") or []
    check("[CP1-X07] discovery found a paid option for every one of the nine real companies in the listing",
          len(paid) == len(EXPECTED_PAID_FLAGSHIPS), paid)
    paid_models = {p.get("model") for p in paid}
    check("[CP1-X07] every paid suggestion is exactly its company's real flagship "
          "(mistral-large-4-0, newest qwen3.8-max, llama-4-maverick, gemini-2.5-pro, newest gpt, "
          "glm-5.3, newest deepseek/kimi/grok)",
          paid_models == EXPECTED_PAID_FLAGSHIPS, paid_models)
    offenders = [p for p in paid if any(w in str(p.get("model", "")).lower() for w in NEVER_PICK_WORDS)]
    check("[CP1-X07] no rejected word (small/guard/image/preview/flash/oss) ever appears in a paid "
          "suggestion", offenders == [], offenders)
    check("[CP1-X07] no rejected word ever appears in the flat pick either",
          not any(w in str(model or "").lower() for w in NEVER_PICK_WORDS), model)


TESTS += [
    ("CP1-X07", x07_realistic_listing_always_picks_each_companys_flagship),
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
