#!/usr/bin/env python3
"""
Reviewer choice and review rules:   python3 tests/test_providers.py

- what blocks: the severity bar by risk, and later rounds scoped to the code that changed
- who reviews: other agent CLIs registered as providers (found, tested and added), another company first at every
  risk level, then the host's company on a different model, then the host itself
- which model each role uses

Every agent CLI here is a fake shell script on a private PATH, so no real CLI is ever called.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gates as g  # noqa: E402  (project helpers: project, approve_plan, to_adversarial, say, rx, ...)

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


FAKE = r"""#!/bin/sh
# fake agent CLI: answers the connectivity check, otherwise returns a review with no findings.
# A file <name>.fail next to it makes it fail like a signed-out or out-of-quota CLI.
me=$(basename "$0")
here=$(dirname "$0")
if [ -f "$here/$me.fail" ]; then echo "error: quota exceeded" >&2; exit 1; fi
out=""
args="$*"
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
case "$input $args" in
  *"connectivity check"*) reply="RATCHET-PROBE-OK";;
  *) reply='{"findings": []}';;
esac
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""


def fake_bin(*names, failing=()):
    b = Path(tempfile.mkdtemp(prefix="ratchet-fakebin-"))
    for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
        p = shutil.which(n)
        if p:
            (b / n).symlink_to(p)
    for n in names:
        (b / n).write_text(FAKE)
        (b / n).chmod(0o755)
    for n in failing:
        (b / f"{n}.fail").write_text("x")
    return b


def env_with(binpath, host="anthropic"):
    e = dict(g.ENV)
    e["PATH"] = str(binpath)
    e["RATCHET_HOST_PROVIDER"] = host
    return e


def rx(d, *args, env=None):
    return g.rx(d, *args, env=env)


def write_review(d, rnd, rid, findings=None):
    return g.write_review(d, rnd, rid, findings=findings)


def ids(rnd):
    return [r["id"] for r in rnd["reviewers"]]


def open_round(d, env=None):
    r = rx(d, "reviewers", "CP1", "--json", env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def engine():
    spec = importlib.util.spec_from_file_location("ratchet_engine_tp", g.ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def severity_and_scope():
    print("What blocks: the severity bar by risk")
    d = g.project("sevlow")                         # the default test checkpoint is low risk
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = open_round(d)
    minor = [{"id": "M1", "severity": "minor", "file": "src/f.js", "line": 1, "description": "name could be clearer"}]
    r = rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, ids(rnd)[0], minor))
    check("low risk: a minor finding alone doesn't block", r.returncode == 0 and "recorded for later" in r.stdout,
          r.stdout + r.stderr)
    r = rx(d, "deferred")
    check("`deferred` lists it in plain words", "name could be clearer" in r.stdout and "CP1" in r.stdout, r.stdout)
    check("`deferred --json` carries the reason", "low-risk step" in rx(d, "deferred", "--json").stdout)
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    check("the round records 1 deferred, 0 blocking",
          st["checkpoints"]["CP1"]["gates"]["adversarial"]["rounds"][-1].get("deferred") == 1)

    for label, sev in (("major", "major"), ("an unknown severity", "critical"), ("no severity", "")):
        d = g.project("sevlow2")
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd = open_round(d)
        f = [{"id": "X1", "severity": sev, "file": "src/f.js", "description": "breaks"}]
        r = rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, ids(rnd)[0], f))
        check(f"low risk: {label} still blocks", r.returncode == 2, r.stdout + r.stderr)

    print("What blocks: normal risk, and later rounds scoped to what changed")
    d = g.project("sevnorm", extra_cp={"risk": "normal"})
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = open_round(d)
    check("normal risk asks two reviewers", len(ids(rnd)) == 2, ids(rnd))
    minor_f = [{"id": "M1", "severity": "minor", "file": "src/f.js", "description": "tiny"}]
    paths = [write_review(d, rnd, ids(rnd)[0], minor_f), write_review(d, rnd, ids(rnd)[1])]
    r = rx(d, "adv-record", "CP1", "--reviews", *paths)
    check("normal risk: a minor finding blocks in round 1", r.returncode == 2, r.stdout + r.stderr)
    (d / "src/f.js").write_text("export const f = () => 1; // fixed\n")
    rx(d, "behavior", "CP1")
    rnd = open_round(d)
    check("round 2 tells reviewers where to look and what blocks",
          rnd["focus_files"] == ["src/f.js"] and any("changed since the last round" in x for x in rnd["review_rules"]),
          (rnd.get("focus_files"), rnd.get("review_rules")))
    elsewhere = [{"id": "M2", "severity": "minor", "file": "src/a.js", "description": "old code nit"}]
    here = [{"id": "M3", "severity": "minor", "file": "./src/f.js", "description": "new code nit"}]
    r = rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, ids(rnd)[0], elsewhere),
           write_review(d, rnd, ids(rnd)[1], here))
    check("round 2: a minor finding in the changed code still blocks", r.returncode == 2, r.stdout + r.stderr)
    open_f = json.loads(next((d / ".ratchet/runs/f/evidence/CP1/adversarial").glob("round-02/open-findings.json")).read_text())
    check("… while a minor finding in untouched code is noted, not sent to the fixer",
          [x["id"] for x in open_f] == ["M3"], open_f)
    (d / "src/f.js").write_text("export const f = () => 1; // fixed again\n")
    rx(d, "behavior", "CP1")
    rnd = open_round(d)
    major_elsewhere = [{"id": "B1", "severity": "major", "file": "src/a.js", "description": "real bug"}]
    r = rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, ids(rnd)[0], major_elsewhere),
           write_review(d, rnd, ids(rnd)[1]))
    check("round 3: a major finding in untouched code still blocks", r.returncode == 2, r.stdout + r.stderr)


def review_holes():
    print("Fresh-eyes findings: ways around the bar must not work")
    # a minor finding the fixer ignored is not waved through in round 2
    d = g.project("carry", extra_cp={"risk": "normal"}, config=["max_adversarial_rounds=20"])
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = open_round(d)
    nit = [{"id": "M1", "severity": "minor", "file": "src/f.js", "description": "unchecked input"}]
    rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, ids(rnd)[0], nit), write_review(d, rnd, ids(rnd)[1]))
    rnd = open_round(d)                                      # the fixer changed nothing
    r = rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, ids(rnd)[0], nit), write_review(d, rnd, ids(rnd)[1]))
    check("a minor finding the fixer ignored still blocks in round 2", r.returncode == 2, r.stdout + r.stderr)
    # path spellings of a changed file can't move a finding out of scope
    (d / "src/f.js").write_text("export const f = () => 1; // touched\n")
    rx(d, "behavior", "CP1")
    for label, path in (("absolute", str(d / "src/f.js")), ("with #L12", "src/f.js#L12"), ("(line 3)", "src/f.js (line 3)"),
                        ("bare name", "f.js"), ("no file", None)):
        rnd = open_round(d)
        f = [{"id": "M9", "severity": "minor", "description": "nit", **({"file": path} if path else {})}]
        r = rx(d, "adv-record", "CP1", "--reviews", write_review(d, rnd, ids(rnd)[0], f), write_review(d, rnd, ids(rnd)[1]))
        check(f"a minor finding in changed code given as {label} still blocks", r.returncode == 2, r.stdout + r.stderr)

    # a provider that runs its own command can't be slipped in after approval
    b = fake_bin("codex")
    env = env_with(b)
    d = g.project("trust", extra_cp={"risk": "high"})
    g.approve_plan(d)
    stamp = Path(tempfile.mkdtemp()) / "stamp.sh"
    stamp.write_text("#!/bin/sh\necho RATCHET-PROBE-OK\necho '{\"findings\": []}'\n")
    stamp.chmod(0o755)
    r = rx(d, "providers", "--add", "rubber", "--command", json.dumps([str(stamp)]), "--company", "acme", env=env)
    check("adding a custom-command provider mid-run is possible…", r.returncode == 0, r.stderr)
    r = rx(d, "tests-written", "CP1", env=env)
    check("… but the run stops until the user confirms it", r.returncode != 0 and "custom review commands" in r.stderr,
          r.stderr)
    d = g.project("trust2", extra_cp={"risk": "high"})
    g.approve_plan(d)
    rx(d, "providers", "--add", "codex", "--preset", "codex", "--model", "gpt-5", env=env)
    g.write_tests(d)
    r = rx(d, "tests-written", "CP1", env=env)
    check("adding a preset tool mid-run needs no confirmation", r.returncode == 0, r.stderr)

    # company names are compared case-insensitively and through aliases
    d = g.project("alias")
    for company in ("Anthropic", "claude"):
        rx(d, "providers", "--add", "mine", "--command", json.dumps([str(stamp)]), "--company", company, env=env)
        rows = {x["id"]: x for x in json.loads(rx(d, "providers", "--json", env=env).stdout)["providers"]}
        check(f"--company {company} on an Anthropic host is the same company, not an independent reviewer",
              rows["mine"]["ok"] is False and "same company" in rows["mine"]["state"], rows.get("mine"))

    # a run approved under the old shipped defaults isn't stopped by the update
    eng = engine()
    d = g.project("upgrade")
    g.approve_plan(d)
    os.environ["RATCHET_HOST_PROVIDER"] = "anthropic"
    os.environ["RATCHET_HOME"] = g.ENV["RATCHET_HOME"]
    ctx = eng.Ctx(d)
    st = ctx.state()
    st["approved"]["rules"]["review_depth"] = eng._sha(eng.OLD_DEFAULT_REVIEW_DEPTH)
    st["approved"]["rules"]["reviewers"] = eng._sha(eng.OLD_DEFAULT_REVIEWERS)
    probs = eng.integrity_problems(ctx, st)
    check("an older run whose rules were the old defaults isn't stopped by the new defaults", not probs, probs)
    rx(d, "config", "--set", 'review_depth={"low":["host-both"],"normal":["host-both"],"high":["host-both"]}')
    probs = eng.integrity_problems(ctx, st)
    check("… but a review_depth someone actually changed still stops it", any("review_depth" in p for p in probs), probs)


def provider_registry():
    eng = engine()
    print("Which company a model belongs to")
    for model, company in [("gpt-5", "openai"), ("o3", "openai"), ("claude-sonnet-5", "anthropic"),
                           ("anthropic/claude-opus", "anthropic"), ("gemini-3-pro", "google"), ("grok-4", "xai"),
                           ("glm-5.3", "zhipu"), ("qwen3-coder", "alibaba"), ("deepseek-v4", "deepseek"),
                           ("kimi-k2", "moonshot"), ("mystery-model", None), ("", None)]:
        check(f"{model or '(none)'} → {company}", eng.company_of_model(model) == company, eng.company_of_model(model))

    print("Finding and testing other agent CLIs")
    b = fake_bin("codex", "gemini", "claude", "cursor-agent", failing=("gemini",))
    env = env_with(b)
    d = g.project("prov", extra_cp={"risk": "normal"})
    rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"},{"id":"gemini","preset":"gemini"},'
                             '{"id":"claude","preset":"claude"}]', env=env)
    r = rx(d, "providers", "--discover", "--json", env=env)
    j = json.loads(r.stdout)
    rows = {x["id"]: x for x in j["providers"]}
    check("discover: a working tool from another company is ready", rows["codex"]["ok"] is True, rows.get("codex"))
    check("discover: a tool that fails its test request is reported as failing, with the reason",
          rows["gemini"]["ok"] is False and "quota exceeded" in rows["gemini"]["state"], rows.get("gemini"))
    check("discover: a tool from the host's own company is not counted as independent",
          rows["claude"]["ok"] is False and "same company" in rows["claude"]["state"], rows.get("claude"))
    check("discover: a tool that runs several companies' models asks for a model", "cursor" in j["needs_model"], j)
    r = rx(d, "providers", "--add", "cursor", "--preset", "cursor", env=env)
    check("adding a multi-company tool without a model is refused", r.returncode != 0 and "--model" in r.stderr, r.stderr)
    r = rx(d, "providers", "--add", "cursor", "--preset", "cursor", "--model", "grok-4", env=env)
    check("adding it with a model tests it, works out the company and adds it",
          r.returncode == 0 and "xai" in r.stdout, r.stdout + r.stderr)
    cfgj = json.loads((d / ".ratchet/config.json").read_text())
    check("the provider is saved in the shared config",
          any(p["id"] == "cursor" and p.get("model") == "grok-4" for p in cfgj["providers"]), cfgj.get("providers"))
    r = rx(d, "providers", "--add", "broken", "--command", '["nope-cli", "{prompt}"]', "--company", "acme", env=env)
    check("a tool that doesn't answer the test request isn't added", r.returncode != 0 and "Not added" in r.stderr, r.stderr)

    print("Another company first, at every risk level")
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = open_round(d, env)
    check("normal risk: two reviewers from two different other companies",
          sorted(r["vendor"] for r in rnd["reviewers"]) == ["openai", "xai"], rnd["reviewers"])
    for r in rnd["reviewers"]:
        out = r["output"]
        p = subprocess.run([sys.executable, str(EXT), "--reviewer", r["id"], "--cp", "CP1", "--out", out],
                           cwd=d, capture_output=True, text=True, env=env)
        check(f"external_review runs {r['id']} through the fake tool", p.returncode == 0 and Path(out).exists(),
              p.stderr[-300:])
    r = rx(d, "adv-record", "CP1", "--reviews", *[x["output"] for x in rnd["reviewers"]], env=env)
    check("their reviews are recorded and approve", r.returncode == 0, r.stdout + r.stderr)
    vd = json.loads((d / ".ratchet/runs/f/viewer-data.json").read_text())
    who = vd["reviews"]["CP1"]["rounds"][-1]["who"]
    check("the viewer names who reviewed the round, with their model",
          any(w.startswith("Codex") for w in who) and any("grok-4" in w for w in who), who)
    check("… and doesn't warn about same-company reviews", vd["reviews"]["CP1"]["rounds"][-1]["own_company_only"] is False)

    print("A provider that stops working hands its slot on")
    b2 = fake_bin("codex", failing=("codex",))
    env2 = env_with(b2)
    d = g.project("provfail")
    rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex"}]', "probe_ttl_minutes=0", env=env2)
    g.approve_plan(d)
    g.to_adversarial(d)
    r = rx(d, "reviewers", "CP1", "--json", env=env2)
    rnd = json.loads(r.stdout)
    check("low risk: codex fails its test request, so this agent on a different model reviews",
          ids(rnd) == ["host-alt-both"] and "checked codex: not usable" in r.stderr, (ids(rnd), r.stderr[-300:]))
    check("the reviewer is told which model to use (not the session's own)",
          "DIFFERENT from yours" in rnd["reviewers"][0]["how"], rnd["reviewers"][0]["how"])

    print("Capable for this risk")
    b3 = fake_bin("codex")
    env3 = env_with(b3)
    d = g.project("provrisk", extra_cp={"risk": "high"})
    rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex","model":"gpt-5-mini","for_risk":["low"]}]', env=env3)
    g.approve_plan(d)
    g.to_adversarial(d)
    rnd = open_round(d, env3)
    check("a provider limited to low-risk steps is not used on a high-risk one",
          not any(i.startswith("codex") for i in ids(rnd)) and len(ids(rnd)) == 4, ids(rnd))

    print("Plan summary names who reviews and which models")
    d = g.project("provsum", extra_cp={"risk": "normal"})
    rx(d, "config", "--set", 'providers=[{"id":"codex","preset":"codex","model":"gpt-5"}]', env=env3)
    s = rx(d, "plan-summary", env=env3).stdout
    check("with a working provider the plan names it and its model, and gives no same-company warning",
          "Codex, gpt-5" in s and "no other provider is available" not in s, s)
    check("the plan lists the models per role", "Models:" in s and "sonnet for" in s and "writing tests" in s, s)


def models_and_hosts():
    print("Models by role, and hosts without alternates")
    d = g.project("models")
    j = json.loads(rx(d, "models", "--json").stdout)
    check("anthropic host: tests are written with sonnet, code with the session's model",
          j["roles"]["ratchet-test-writer"] == "sonnet" and j["roles"]["ratchet-implementer"] == "inherit", j)
    e = dict(g.ENV, RATCHET_HOST_PROVIDER="github")
    j = json.loads(rx(d, "models", "--json", env=e).stdout)
    check("a host with no model table runs every role on the session's model",
          set(j["roles"].values()) == {"inherit"}, j)
    check("host aliases are understood (claude → anthropic)",
          json.loads(rx(d, "models", "--json", env=dict(g.ENV, RATCHET_HOST_PROVIDER="claude")).stdout)["host"] == "anthropic")
    saved = g.ENV["RATCHET_HOST_PROVIDER"]
    g.ENV["RATCHET_HOST_PROVIDER"] = "github"   # the whole run on a host with no alternate models
    try:
        d = g.project("models2")
        g.approve_plan(d)
        g.to_adversarial(d)
        rnd = open_round(d)
    finally:
        g.ENV["RATCHET_HOST_PROVIDER"] = saved
    check("with no other provider and no alternate model, the host reviews itself", ids(rnd) == ["host-both"], ids(rnd))

    print("An untouched old default is moved to the new one")
    d = g.project("migr")
    eng = engine()
    rx(d, "config", "--set", "review_depth=" + json.dumps(eng.OLD_DEFAULT_REVIEW_DEPTH))
    cfg = json.loads(rx(d, "config").stdout)
    check("old default review_depth reads as the new one", cfg["review_depth"]["low"] == ["any-both"], cfg["review_depth"])
    custom = {"low": ["host-both"], "normal": ["host-standards"], "high": ["host-standards", "host-correctness"]}
    rx(d, "config", "--set", "review_depth=" + json.dumps(custom))
    check("a customised review_depth is left alone", json.loads(rx(d, "config").stdout)["review_depth"] == custom)


def main():
    severity_and_scope()
    review_holes()
    provider_registry()
    models_and_hosts()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
