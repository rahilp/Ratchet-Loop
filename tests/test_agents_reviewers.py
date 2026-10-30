#!/usr/bin/env python3
"""
CP5 tests: choose the outside reviewer's provider and model.   python3 tests/test_agents_reviewers.py

`agents.reviewer` (and `agents.visual-reviewer` / `agents.behavior-reviewer` for the two UI lenses) takes a provider
(`codex`) or a provider and a model (`codex/gpt-5.1`), in the project's config.json or in the machine-wide file under
the per-user Ratchet home (the project's value wins). The chosen provider is tried FIRST for its review slot and the
chosen model is what really runs and what the review record shows. Nothing else about a review changes: the number of
reviewers per risk level, the rule that a company not yet reviewing comes before a repeat, the pay-per-use consent
rule (a choice never grants it, and a paid provider never ranks over a usable flat-rate one), the share cap, the
same-company rule, explicit reviewer ids in review_depth and a checkpoint's own `reviewers` pin (their written order
stays; the choice is reported as not applied), the fallbacks, and the way verdicts are counted. An unknown reviewer
provider is refused before any work starts. The UI review path honors the same paid-consent rule.

Everything is driven in-process through the engine's own entry points (standard 30): the engine module is loaded once
per process by tests/trust_helpers.py; resolve_reviewers and the CLI commands run inside throwaway git projects, each
with its own private per-user Ratchet home. The few cases that need a real child process (external_review.py and
ui_review.py are scripts that run an outside CLI) call that script with stub outside-reviewer CLIs on a private PATH;
the stubs record their argv. No network, no real provider CLI, nothing is spent.

The default-parity cases (T01, T02, T19, T22) compare against output recorded from the engine BEFORE this change and
written into this file as plain values; they do not recompute a selection.

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import datetime as dt
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
import test_agents_config as ac                         # noqa: E402  (jload(), to_review())

EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
UI_REVIEW = ROOT / "skills" / "ratchet-ui-compare" / "scripts" / "ui_review.py"

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP5-[TX]\d+)\]")


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

# Roster: two flat-rate outside providers of different companies (gemini: google, codex: openai) and one pay-per-use
# provider (zpay: zhipu); the host is Claude Code (anthropic) unless a case says otherwise.
GEMINI = {"id": "gemini", "preset": "gemini"}
CODEX = {"id": "codex", "preset": "codex"}
PAID = {"id": "zpay", "preset": "opencode", "model": "opencode-go/glm-5.3", "company": "zhipu", "cost": "per-use"}
PAID_OK = dict(PAID, paid_ok_at="2026-01-01T00:00:00+00:00")   # the developer's own recorded OK, written by the test
CLAUDE = {"id": "claude", "preset": "claude"}                  # the same company as the anthropic host
ROSTER = [GEMINI, CODEX, PAID]
RISKS = ("low", "normal", "high")
FAR = "2099-01-01T00:00:00+00:00"
LONG_AGO = "2000-01-01T00:00:00+00:00"
PAY_REASON = ("costs money per use and the developer has not given the exact OK for this model (no paid_ok_at), so it "
              "is never used or sent a request")
DIFFERENT = "provider already reviewing this round; preferring a different one"

# What resolve_reviewers returned for ROSTER with no setting anywhere, recorded from the unmodified engine:
# {risk: [(id, slot, fallback_for, reasons)]}.
GOLDEN = {
    "low": [("gemini-both", 1, [], {"zpay-both": PAY_REASON})],
    "normal": [
        ("gemini-standards", 1, [], {"zpay-standards": PAY_REASON}),
        ("codex-correctness", 2, ["gemini-correctness"], {"zpay-correctness": PAY_REASON, "gemini-correctness": DIFFERENT}),
    ],
    "high": [
        ("gemini-standards", 1, [], {"zpay-standards": PAY_REASON}),
        ("codex-correctness", 2, ["gemini-correctness"], {"zpay-correctness": PAY_REASON, "gemini-correctness": DIFFERENT}),
        ("gemini-correctness", 3, [], {"zpay-correctness": PAY_REASON}),
        ("codex-standards", 4, [], {"zpay-standards": PAY_REASON}),
    ],
}
GOLDEN_COUNT = {"low": 1, "normal": 2, "high": 4}
DEFAULT_REVIEW_DEPTH = {"low": ["any-both"], "normal": ["any-standards", "any-correctness"],
                        "high": ["any-standards", "any-correctness", "any-correctness", "any-standards"]}
DEFAULT_BLOCKING = {"low": ["blocker", "major"], "normal": "all", "high": "all"}

# One stub outside CLI for every name. It records "<name>|<kind>|<argv>" in a log baked into the script, answers a
# connectivity check with RATCHET-PROBE-OK and a review request with the reply in "<name>.reply" beside it (an
# approval with no findings when there is none). Like a codex-style CLI it also writes its reply to the file named by
# --output-last-message.
STUB = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
args="$*"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output-last-message) out="$2"; shift 2;;
    *) shift;;
  esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
kind=review
case "$input $args" in *"connectivity check"*) kind=probe;; esac
printf '%s|%s|%s\n' "$me" "$kind" "$args" >> "@LOG@"
if [ "$kind" = probe ]; then reply="RATCHET-PROBE-OK"
elif [ -f "$here/$me.reply" ]; then reply=$(cat "$here/$me.reply")
else reply='{"findings": []}'; fi
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""

# The UI review runners (claude, codex): the log line says which lens the prompt was for.
UI_STUB = r"""#!/bin/sh
me=$(basename "$0")
args="$*"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output-last-message) out="$2"; shift 2;;
    *) shift;;
  esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
role=unknown
case "$input $args" in
  *"Do the visual review"*) role=visual;;
  *"Do the behavior review"*) role=behavior;;
esac
printf '%s|%s|%s\n' "$me" "$role" "$args" >> "@LOG@"
rev='{"reviewer":"x","verdict":"PASS","differences":[]}'
if [ "$me" = claude ]; then printf '{"structured_output": %s}\n' "$rev"
else
  if [ -n "$out" ]; then printf '%s' "$rev" > "$out"; else printf '%s\n' "$rev"; fi
fi
"""


class Stubs:
    """A private folder of stub CLIs put first on `env`'s PATH. `calls()` reads what they logged."""

    def __init__(self, env, names, script=STUB):
        self.bin = Path(_tmp.mkdtemp(prefix="ratchet-agrev-bin-"))
        self.log = self.bin / "calls.log"
        for n in names:
            p = self.bin / n
            p.write_text(script.replace("@LOG@", str(self.log)))
            p.chmod(0o755)
        env["PATH"] = f"{self.bin}:{env['PATH']}"

    def calls(self, name=None, kind=None):
        """[(program, kind-or-lens, argv text)] in the order the stubs ran. A record starts at a line of the form
        '<program>|<word>|'; an argument that holds a newline (a prompt) continues the record."""
        text = self.log.read_text() if self.log.exists() else ""
        out = []
        for rec in re.split(r"(?m)^(?=[A-Za-z0-9_.-]+\|[a-z]+\|)", text):
            parts = rec.rstrip("\n").split("|", 2)
            if len(parts) == 3:
                out.append(parts)
        return [c for c in out if (name is None or c[0] == name) and (kind is None or c[1] == kind)]

    def reply(self, name, text):
        (self.bin / f"{name}.reply").write_text(text)


def world(proj=None, glob=None, providers=None, names=("codex", "gemini", "opencode", "claude")):
    """A throwaway git project (planning phase, with a run) plus a private env. `providers` goes into the project's
    config (default ROSTER); `names` are the stub CLIs installed. Returns (dir, env, stubs)."""
    cfg = dict(proj or {})
    cfg.setdefault("providers", [dict(p) for p in (providers if providers is not None else ROSTER)])
    d, env = cl.project(cfg, glob=glob)
    return d, env, Stubs(env, names)


class Res:
    def __init__(self, out, rc, err):
        self.out, self.rc, self.err = out, rc, err

    @property
    def ids(self):
        return [x["id"] for x in self.out or []]

    def slot(self, n):
        return next((x for x in self.out or [] if x.get("slot") == n), {})

    def shape(self):
        return [(x["id"], x["slot"], x["fallback_for"], x["reasons"]) for x in self.out or []]


def resolve(d, env, risk="normal", pin=None, share=False):
    """resolve_reviewers over the project's effective config, in-process. `share`: apply the shared provider record's
    share cap the way a round being opened does."""
    m = cl.engine()
    with th.InProcessCall(m, d, env, cl.RESET_GLOBALS, cl.LOCK_WAIT_CAP) as r:
        ctx = m.Ctx(m.find_root())
        cfg = ctx.config()
        cp = {"id": "CP1", "risk": risk}
        if pin:
            cp["reviewers"] = list(pin)
        sh = {"data": m.provider_health(), "ignore": set()} if share else None
        print(json.dumps(m.resolve_reviewers(ctx, cfg, cp, share=sh)))
    try:
        out = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        out = None
    return Res(out, r.returncode, r.stderr)


def health_key(pv):
    return cl.engine()._health_key(pv)


def write_unavailable(d, rids, reason="out of quota"):
    (Path(d) / ".ratchet" / "unavailable-reviewers.json").write_text(json.dumps(
        {rid: {"reason": reason, "since": LONG_AGO, "until": FAR} for rid in rids}))


def write_bench(env, pv):
    p = Path(env["RATCHET_HOME"]) / "provider-health.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({health_key(pv): {"bench": {"reason": "out of quota", "since": LONG_AGO, "until": FAR,
                                                        "quota": True}}}))


def write_start(env, pv):
    p = Path(env["RATCHET_HOME"]) / "provider-health.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
    p.write_text(json.dumps({health_key(pv): {"starts": [{"at": at, "id": "earlier-start"}]}}))


def models_row(d, env, role="reviewer"):
    r = cl.run(d, env, "models", "--json")
    data = ac.jload(r)
    rows = {e["role"]: e for e in (data or {}).get("detail", []) if isinstance(e, dict) and "role" in e}
    return rows.get(role) or {}, r


def s(v) -> str:
    return json.dumps(v) if not isinstance(v, str) else v


def tree_text(*bases):
    blob = ""
    for base in bases:
        base = Path(base)
        if base.is_file():
            blob += base.read_text(errors="ignore")
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file():
                blob += p.read_text(errors="ignore")
    return blob


# ---- an approved run taken to the review step, for the cases that go through `reviewers` and the review scripts

def approved_world(cfg, glob=None, cp_extra=None, host="claude", stub_names=("codex", "gemini"), replies=None):
    """A throwaway project with one checkpoint approved into building (mirrors tests/test_agents_config.py approved(),
    with the project's whole config and the checkpoint chosen by the caller), stub CLIs installed, taken to the
    adversarial step. `cfg` is the project's config dict, or a function of the stub folder that returns it (for a
    provider whose command is one of the stubs). Returns (dir, env, stubs, the `reviewers CP1 --json` round)."""
    env = th.make_env(host)
    if glob is not None:
        cl.put_global(env, glob)
    stubs = Stubs(env, stub_names)
    for name, text in (replies or {}).items():
        stubs.reply(name, text)
    if callable(cfg):
        cfg = cfg(stubs)
    d = Path(_tmp.mkdtemp(prefix="ratchet-agrev-ap-"))
    th.git(d, "init", "-q")
    th.git(d, "config", "user.email", "t@t")
    th.git(d, "config", "user.name", "t")
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    th.git(d, "add", "-A")
    th.git(d, "commit", "-qm", "init")
    (d / ".ratchet").mkdir(exist_ok=True)
    body = dict(cfg, min_review_seconds=0)
    cl.project_path(d).write_text(json.dumps(body))
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
    cp.update(cp_extra or {})
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
    th.cp3_write_tests(d)
    for a in (("tests-written", "CP1"), ("red", "CP1")):
        r = cl.run(d, env, *a)
        assert r.returncode == 0, cl.text(r)
    (d / "src/f.js").write_text("export const f = () => 1;\n")
    for a in (("implemented", "CP1"), ("behavior", "CP1")):
        r = cl.run(d, env, *a)
        assert r.returncode == 0, cl.text(r)
    first = cl.run(d, env, "reviewers", "CP1", "--json")
    data = ac.jload(first)
    assert first.returncode == 0 and isinstance(data, dict), cl.text(first)
    stubs.first_call = first    # what the call that opened the round printed (the notes it gave are on its streams)
    return d, env, stubs, data


def run_review_script(d, env, rid, out_path, cp="CP1"):
    """The agent runs one outside reviewer through external_review.py (a real child process)."""
    return subprocess.run([sys.executable, str(EXT), "--reviewer", rid, "--cp", cp, "--out", str(out_path),
                           "--root", str(d)], cwd=d, capture_output=True, text=True, env=env, timeout=240)


def reviewer_entry(data, rid):
    return next((x for x in data.get("reviewers", []) if x.get("id") == rid), {})


def stub_provider(stubs, name="stubcli", pid="stub", company="stubco", **kw):
    """A hand-written provider whose command is one of the stub CLIs (the chosen model goes where {model} is)."""
    return dict({"id": pid, "company": company, "stdin": True,
                 "command": [str(stubs.bin / name), "--model", "{model}", "-"]}, **kw)


def stub_review(agents_value, glob=None):
    """An approved low-risk run whose only provider is the stub CLI `stub`, agents.reviewer = agents_value, taken to
    the adversarial step. Returns (dir, env, stubs, the `reviewers CP1 --json` round)."""
    return approved_world(lambda st: {"providers": [stub_provider(st)], "agents": {"reviewer": agents_value}},
                          glob=glob, stub_names=("stubcli",))


# ------------------------------------------------------------------------------------------------ cases

def t01_no_choice_same_reviewers():
    d, env, _ = world()
    for risk in RISKS:
        got = resolve(d, env, risk)
        check(f"[CP5-T01] {risk} risk: resolve_reviewers runs", got.out is not None, got.err[-300:])
        check(f"[CP5-T01] {risk} risk: ids, slots, fallback_for and reasons equal the recorded output exactly",
              got.shape() == [tuple(g) for g in GOLDEN[risk]], got.shape())


def t02_empty_agents_changes_nothing():
    d, env, _ = world({"agents": {}})
    d0, env0, _ = world()
    for risk in RISKS:
        got, base = resolve(d, env, risk), resolve(d0, env0, risk)
        check(f"[CP5-T02] {risk} risk: an empty agents setting gives the recorded output",
              got.out is not None and got.shape() == [tuple(g) for g in GOLDEN[risk]], got.shape())
        check(f"[CP5-T02] {risk} risk: byte for byte the same as with no agents setting at all",
              got.out is not None and json.dumps(got.out, sort_keys=True) == json.dumps(base.out, sort_keys=True),
              (got.err[-200:], base.err[-200:]))


def t03_chosen_provider_first():
    d, env, _ = world({"agents": {"reviewer": "codex"}, "review_depth": {"normal": ["any-correctness"]}})
    got = resolve(d, env, "normal")
    one = got.slot(1)
    check("[CP5-T03] slot 1 resolves to codex's reviewer id", got.ids == ["codex-correctness"], (got.ids, got.err[-300:]))
    check("[CP5-T03] gemini is not skipped for a reason (it simply ranks after)",
          bool(one) and "gemini-correctness" not in one.get("fallback_for", [])
          and "gemini-correctness" not in one.get("reasons", {}), one)
    d2, env2, _ = world({"review_depth": {"normal": ["any-correctness"]}})
    base = resolve(d2, env2, "normal")
    check("[CP5-T03] without the choice the same project picks gemini first (the choice is what moved it)",
          base.ids == ["gemini-correctness"], base.ids)


def t04_provider_model_launches_that_model():
    prov = {"id": "codex", "preset": "codex", "model": "gpt-5"}
    cfg = {"providers": [prov], "agents": {"reviewer": "codex/gpt-5.1"}}
    d, env, stubs, data = approved_world(cfg)
    rv = reviewer_entry(data, "codex-both")
    check("[CP5-T04] the round's reviewer is codex-both", [x["id"] for x in data.get("reviewers", [])] == ["codex-both"],
          [x.get("id") for x in data.get("reviewers", [])])
    got = resolve(d, env, "low")
    check("[CP5-T04] resolve_reviewers reports model gpt-5.1, not the preset's gpt-5",
          got.out is not None and got.out[0].get("model") == "gpt-5.1", (got.out, got.err[-200:]))
    out = Path(rv.get("output", d / "missing.json"))
    ext = run_review_script(d, env, "codex-both", out)
    review_calls = stubs.calls("codex", "review")
    argv = review_calls[-1][2] if review_calls else ""
    check("[CP5-T04] the review script ran the reviewer", ext.returncode == 0 and len(review_calls) == 1,
          (ext.returncode, ext.stderr[-300:], stubs.calls()))
    check("[CP5-T04] the launched command carries gpt-5.1 in the model position",
          re.search(r"(^| )-m gpt-5\.1( |$)", argv) is not None, argv)
    check("[CP5-T04] the preset's own gpt-5 is not what was launched", re.search(r"(^| )-m gpt-5( |$)", argv) is None, argv)


def t05_chosen_provider_leads_every_slot():
    d, env, _ = world({"agents": {"reviewer": "codex"}})
    got = resolve(d, env, "normal")
    check("[CP5-T05] codex leads slot 1, and slot 2 still moves to a different company",
          got.ids == ["codex-standards", "gemini-correctness"], (got.ids, got.err[-300:]))
    two = got.slot(2)
    check("[CP5-T05] slot 2 shows codex was set aside, with the existing reason",
          two.get("fallback_for") == ["codex-correctness"]
          and (two.get("reasons") or {}).get("codex-correctness") == DIFFERENT, two)
    one = got.slot(1)
    check("[CP5-T05] slot 1 shows nothing set aside: codex simply leads",
          one.get("fallback_for") == [], one)


def t06_number_of_reviewers_never_changes():
    d, env, _ = world({"agents": {"reviewer": "codex/gpt-5.1"}}, glob={"agents": {"reviewer": "codex/gpt-5.1"}})
    for risk in RISKS:
        got = resolve(d, env, risk)
        check(f"[CP5-T06] {risk} risk: the same number of reviewers as today ({GOLDEN_COUNT[risk]})",
              got.out is not None and len(got.out) == GOLDEN_COUNT[risk], (got.ids, got.err[-300:]))
    cfg, r = cl.effective(d, env)
    check("[CP5-T06] review_depth and blocking_severities are the shipped ones",
          isinstance(cfg, dict) and cfg.get("review_depth") == DEFAULT_REVIEW_DEPTH
          and cfg.get("blocking_severities") == DEFAULT_BLOCKING, cl.text(r)[:300])


def t07_paid_choice_needs_the_developers_reply():
    d, env, _ = world({"agents": {"reviewer": "zpay"}})
    before = tree_text(d / ".ratchet", env["RATCHET_HOME"])
    got = resolve(d, env, "normal")
    one = got.slot(1)
    check("[CP5-T07] the paid provider is not picked; the flat-rate ones run",
          got.out is not None and got.ids == ["gemini-standards", "codex-correctness"], (got.ids, got.err[-300:]))
    check("[CP5-T07] the paid provider was tried first and set aside (it is in fallback_for)",
          "zpay-standards" in one.get("fallback_for", []), one)
    why = s((one.get("reasons") or {}).get("zpay-standards")).lower()
    check("[CP5-T07] the reason says it needs the developer's pay-per-use approval",
          re.search(r"per[- ]use", why) is not None and re.search(r"\bok\b|approv", why) is not None, why)
    after = tree_text(d / ".ratchet", env["RATCHET_HOME"])
    check("[CP5-T07] no paid_ok_at is written to any config or record", "paid_ok_at" not in after, after.count("paid_ok_at"))
    check("[CP5-T07] the run changed no config", before.count("paid_ok_at") == 0)


def t08_paid_with_ok_ranks_after_flat():
    d, env, _ = world({"agents": {"reviewer": "zpay"}}, providers=[GEMINI, CODEX, PAID_OK])
    got = resolve(d, env, "normal")
    check("[CP5-T08] the preference does not lift the pay-per-use provider over the usable flat-rate ones",
          got.out is not None and got.ids == ["gemini-standards", "codex-correctness"], (got.ids, got.err[-300:]))
    d2, env2, _ = world({"agents": {"reviewer": "zpay"}}, providers=[GEMINI, CODEX, PAID_OK])
    write_unavailable(d2, [f"{p}-{lens}" for p in ("gemini", "codex") for lens in ("both", "standards", "correctness")])
    got2 = resolve(d2, env2, "low")
    check("[CP5-T08] with every flat-rate provider down, the chosen paid one (with the OK) is allowed",
          got2.out is not None and got2.ids == ["zpay-both"], (got2.ids, got2.err[-300:]))


def t09_choice_never_grants_paid_approval():
    d, env, _ = world(glob={"agents": {"reviewer": "zpay"}})
    cfg, r = cl.effective(d, env)
    pv = next((p for p in (cfg or {}).get("providers", []) if p.get("id") == "zpay"), None)
    check("[CP5-T09] config loads and the paid provider has no paid_ok_at", r.returncode == 0 and pv is not None
          and "paid_ok_at" not in pv, cl.text(r)[:300])
    got = resolve(d, env, "normal")
    check("[CP5-T09] reviewers never pick the paid provider", got.out is not None
          and not any(i.startswith("zpay") for i in got.ids), (got.ids, got.err[-300:]))
    row, r2 = models_row(d, env)
    why = s(row.get("reason")).lower()
    check("[CP5-T09] the models view still reports it as needing approval",
          "approv" in why or re.search(r"\bok\b", why) is not None, row)
    check("[CP5-T09] and does not show the paid provider as the reviewer that will be used",
          bool(row.get("effective")) and "zpay" not in s(row.get("effective")).lower(), row)
    blob = tree_text(d / ".ratchet", env["RATCHET_HOME"])
    check("[CP5-T09] paid_ok_at exists nowhere afterwards", "paid_ok_at" not in blob, blob.count("paid_ok_at"))


def t10_same_company_pick_not_independent():
    provs = [GEMINI, CODEX, CLAUDE]
    d, env, _ = world({"agents": {"reviewer": "claude"}}, providers=provs)
    want = {"normal": ["gemini-standards", "codex-correctness"],
            "high": ["gemini-standards", "codex-correctness", "gemini-correctness", "codex-standards"]}
    for risk in ("normal", "high"):
        got = resolve(d, env, risk)
        independent = [x for x in got.out or [] if x.get("vendor") not in ("host", "anthropic")]
        check(f"[CP5-T10] {risk} risk: other companies fill every slot, the same-company pick is not a reviewer",
              got.out is not None and got.ids == want[risk] and len(independent) == GOLDEN_COUNT[risk],
              (got.ids, got.err[-300:]))
        one = got.slot(1)
        why = s((one.get("reasons") or {}).get("claude-standards")).lower()
        check(f"[CP5-T10] {risk} risk: slot 1 shows the same-company pick set aside, saying it is not independent",
              "claude-standards" in one.get("fallback_for", []) and "same company" in why
              and "not an independent" in why, one)


def t11_same_company_only_never_silently_counted():
    d, env, _ = world({"agents": {"reviewer": "claude"}}, providers=[CLAUDE])
    got = resolve(d, env, "low")
    one = got.slot(1)
    why = s((one.get("reasons") or {}).get("claude-both")).lower()
    check("[CP5-T11] the same-company provider is not a reviewer; the host's own sub-agents fill the slot",
          got.out is not None and got.ids == ["host-alt-both"] and all(x.get("vendor") != "anthropic" for x in got.out),
          (got.ids, got.err[-300:]))
    check("[CP5-T11] the result still reports it, as same-company and not independent",
          "claude-both" in one.get("fallback_for", []) and "same company" in why and "not an independent" in why, one)
    d2, env2, _ = world({"agents": {"reviewer": "claude"}}, providers=[CLAUDE])
    check("[CP5-T11] the cross-company requirement is not removed: at normal risk no slot is a claude reviewer",
          not any(i.startswith("claude-") for i in resolve(d2, env2, "normal").ids))


def t12_explicit_ids_keep_written_order():
    cfg = {"providers": [dict(GEMINI), dict(CODEX)], "agents": {"reviewer": "gemini"},
           "review_depth": {"normal": ["codex-both", "gemini-both"]}}
    d, env, stubs, data = approved_world(cfg, cp_extra={"risk": "normal"})
    ids = [x["id"] for x in data.get("reviewers", [])]
    check("[CP5-T12] the written order is kept (codex-both, then gemini-both)", ids == ["codex-both", "gemini-both"], ids)
    r = cl.run(d, env, "reviewers", "CP1", "--json")
    blob = (cl.text(stubs.first_call) + cl.text(r)).lower()
    check("[CP5-T12] gemini is reported as not applied for that slot",
          r.returncode == 0 and "not applied" in blob and "gemini" in blob, blob[-400:])
    got = resolve(d, env, "normal")
    check("[CP5-T12] resolve_reviewers keeps the written order too", got.ids == ["codex-both", "gemini-both"],
          (got.ids, got.err[-300:]))


def t13_checkpoint_pin_keeps_its_order():
    cfg = {"providers": [dict(GEMINI), dict(CODEX)], "agents": {"reviewer": "codex"}}
    d, env, stubs, data = approved_world(cfg, cp_extra={"reviewers": ["host-both"]})
    ids = [x["id"] for x in data.get("reviewers", [])]
    check("[CP5-T13] only the pinned reviewer is used", ids == ["host-both"], ids)
    r = cl.run(d, env, "reviewers", "CP1", "--json")
    blob = (cl.text(stubs.first_call) + cl.text(r)).lower()
    check("[CP5-T13] the output says agents.reviewer was not applied",
          r.returncode == 0 and "agents.reviewer" in blob and "not applied" in blob, blob[-400:])
    got = resolve(d, env, "low", pin=["host-both"])
    check("[CP5-T13] resolve_reviewers with the pin returns just the pinned reviewer", got.ids == ["host-both"],
          (got.ids, got.err[-300:]))


def t14_unavailable_choice_falls_back_plainly():
    d, env, _ = world({"agents": {"reviewer": "codex"}})
    write_unavailable(d, [f"codex-{x}" for x in ("both", "standards", "correctness")])
    got = resolve(d, env, "normal")
    one = got.slot(1)
    check("[CP5-T14] the slots go to the next working provider", got.out is not None
          and got.ids == ["gemini-standards", "gemini-correctness"], (got.ids, got.err[-300:]))
    check("[CP5-T14] codex appears in fallback_for with its reason",
          "codex-standards" in one.get("fallback_for", [])
          and "out of quota" in s((one.get("reasons") or {}).get("codex-standards")), one)
    d2, env2, _ = world({"agents": {"reviewer": "codex"}})
    write_unavailable(d2, [f"{p}-{x}" for p in ("codex", "gemini") for x in ("both", "standards", "correctness")])
    got2 = resolve(d2, env2, "normal")
    check("[CP5-T14] with every outside provider down, host sub-agents remain the last fallback",
          got2.out is not None and got2.ids == ["host-alt-standards", "host-alt-correctness"], (got2.ids, got2.err[-300:]))
    check("[CP5-T14] and codex is still named as skipped, with its reason",
          "codex-standards" in got2.slot(1).get("fallback_for", []), got2.slot(1))


def t15_share_cap_is_not_bypassed():
    d, env, _ = world({"agents": {"reviewer": "codex"}, "provider_share": {"max": 1, "minutes": 15}})
    write_start(env, CODEX)
    got = resolve(d, env, "low", share=True)
    one = got.slot(1)
    why = s((one.get("reasons") or {}).get("codex-both"))
    check("[CP5-T15] the chosen provider at its share cap is skipped; the next provider runs",
          got.out is not None and got.ids == ["gemini-both"], (got.ids, got.err[-300:]))
    check("[CP5-T15] codex is named as skipped with the share reason",
          "codex-both" in one.get("fallback_for", []) and "1 review start" in why and "15 minute" in why, one)


def t16_duplicate_company_model_lens_collapses():
    twin = {"id": "codexb", "preset": "codex", "model": "gpt-5.1"}
    first = {"id": "codex", "preset": "codex", "model": "gpt-5.1"}
    cfg = {"providers": [first, twin], "agents": {"reviewer": "codexb/gpt-5.1"},
           "review_depth": {"normal": ["any-correctness", "any-correctness"]}}
    d, env, _ = world(cfg)
    got = resolve(d, env, "normal")
    check("[CP5-T16] the second slot collapses as it does today: one reviewer, in slot 1",
          got.out is not None and len(got.out) == 1 and got.out[0]["slot"] == 1, (got.ids, got.err[-300:]))
    check("[CP5-T16] the surviving reviewer is the chosen provider's", got.ids == ["codexb-correctness"], got.ids)
    d0, env0, _ = world({"review_depth": {"normal": ["any-correctness", "any-correctness"]}}, providers=[first, twin])
    base = resolve(d0, env0, "normal")
    check("[CP5-T16] with no choice it collapses the same way (one reviewer)",
          base.out is not None and len(base.out) == 1, (base.ids, base.err[-300:]))


def t17_unknown_reviewer_provider_refused_before_work():
    d, env, stubs = world()
    rd = d / ".ratchet/runs/f"
    (rd / "app-scout.json").write_text("{}")
    cp = {"id": "CP1", "title": "Step 1", "summary": "s", "done_when": ["works"],
          "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
          "tests": {"files": ["tests/cp1.test.js"], "command": "test -f src/f.js"}}
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    ok = cl.run(d, env, "plan-submit")
    check("[CP5-T17] setup: the plan is valid without the bad setting", ok.returncode == 0, cl.text(ok)[:300])
    # back to planning: a fresh project with the same plan, then the bad value is written
    d, env, stubs = world()
    rd = d / ".ratchet/runs/f"
    (rd / "app-scout.json").write_text("{}")
    (rd / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": [cp]}))
    (rd / "tests" / "CP1.json").write_text(json.dumps({"cases": [{"id": "CP1-T01", "layer": "code", "proves": [1]}]}))
    pc = cl.project_path(d)
    cfg = json.loads(pc.read_text())
    cfg["agents"] = {"reviewer": "nosuch"}
    pc.write_text(json.dumps(cfg))
    for args in (("plan-submit",), ("reviewers", "CP1", "--json"), ("models", "--json")):
        r = cl.run(d, env, *args)
        out = cl.text(r)
        check(f"[CP5-T17] `{' '.join(args)}` stops, naming nosuch and the file, with no traceback",
              r.returncode != 0 and "nosuch" in out and "config.json" in out and "Traceback" not in out,
              (r.returncode, out[:300]))
        check(f"[CP5-T17] `{' '.join(args)}` lists the valid providers",
              all(p in out for p in ("gemini", "codex", "zpay")), out[:400])
    st = json.loads((rd / "state.json").read_text()) if (rd / "state.json").exists() else {}
    check("[CP5-T17] no plan was submitted and no round was opened", st.get("phase") == "planning"
          and not (st.get("checkpoints") or {}).get("CP1", {}).get("adv_round"), st.get("phase"))
    check("[CP5-T17] nothing was launched (no stub CLI ran)", not stubs.calls(), stubs.calls())


UI_LISTS = {"visual": ["claude", "codex"], "behavior": ["claude", "codex"]}
UI_LISTS_PAID = {"visual": ["codex", "claude"], "behavior": ["codex", "claude"]}


def ui_world(proj=None, glob=None, runners=("claude", "codex"), host="gemini"):
    """A git project with a config and no run (an ad-hoc comparison), stub UI runners on PATH, host company google
    (so claude and codex are both outside reviewers). Returns (dir, env, stubs, manifest path)."""
    env = th.make_env(host)
    if glob is not None:
        cl.put_global(env, glob)
    stubs = Stubs(env, runners, script=UI_STUB)
    d = th.cp5y_repo()
    (d / ".ratchet").mkdir()
    cl.project_path(d).write_text(json.dumps(proj or {}))
    m = d / "shots" / "manifest.json"
    m.parent.mkdir()
    m.write_text(json.dumps({"viewport": {"width": 100, "height": 100}, "states": []}))
    return d, env, stubs, m


_UI = {}


def ui_order(d, env):
    """ui_review.py's own role_providers over the project's settings, in-process: (host, {lens: [runner, ...]}), the
    order each lens tries its runners in (the host's own sub-agents come last, after all of them)."""
    if "mod" not in _UI:
        spec = importlib.util.spec_from_file_location("ratchet_ui_review_cp5", UI_REVIEW)
        _UI["mod"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_UI["mod"])
    ns = type("Args", (), {"visual": None, "behavior": None})()
    m = cl.engine()
    with th.InProcessCall(m, d, env, cl.RESET_GLOBALS, cl.LOCK_WAIT_CAP) as r:
        host, order = _UI["mod"].role_providers(ns)
        print(json.dumps([host, order]))
    try:
        return tuple(json.loads(r.stdout)) if r.returncode == 0 else (None, None)
    except ValueError:
        return None, None


def run_ui_review(d, env, manifest, out_name="out"):
    out = Path(d) / out_name
    r = subprocess.run([sys.executable, str(UI_REVIEW), "review", "--manifest", str(manifest), "--out", str(out),
                        "--scope", "all"], cwd=d, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                       timeout=240)
    return r, out


def ui_ran(stubs):
    """{lens: [(runner, argv)]} from the stub log."""
    got = {}
    for name, role, argv in stubs.calls():
        got.setdefault(role, []).append((name, argv))
    return got


def t18_ui_choices_lead_each_lens():
    proj = {"ui_reviewers": UI_LISTS, "agents": {"visual-reviewer": "codex", "behavior-reviewer": "codex/model-x"}}
    d, env, stubs, m = ui_world(proj)
    host, order = ui_order(d, env)
    check("[CP5-T18] the resolved order puts codex first for both lenses, claude after it",
          order == {"visual": ["codex", "claude"], "behavior": ["codex", "claude"]}, (host, order))
    r, out = run_ui_review(d, env, m)
    ran = ui_ran(stubs)
    check("[CP5-T18] the review ran and wrote a combined result", r.returncode == 0 and (out / "combined.json").exists(),
          (r.returncode, r.stderr[-300:]))
    check("[CP5-T18] codex is first for the visual lens (claude, listed first, never ran)",
          [n for n, _ in ran.get("visual", [])] == ["codex"], ran)
    check("[CP5-T18] codex is first for the behavior lens too", [n for n, _ in ran.get("behavior", [])] == ["codex"], ran)
    beh = ran.get("behavior", [("", "")])[0][1]
    vis = ran.get("visual", [("", "")])[0][1]
    check("[CP5-T18] the behavior lens launched model-x", re.search(r"--model model-x( |$)", beh) is not None, beh)
    check("[CP5-T18] the visual lens, set with a provider only, launched no model flag", "--model" not in vis, vis)
    roles = {k: json.loads((out / f"{k}.json").read_text()).get("provider") for k in ("visual", "behavior")
             if (out / f"{k}.json").exists()}
    check("[CP5-T18] the role files name codex as the provider", roles == {"visual": "codex", "behavior": "codex"}, roles)
    d2, env2, stubs2, m2 = ui_world(proj, runners=("not-a-reviewer",))
    r2, _ = run_ui_review(d2, env2, m2)
    check("[CP5-T18] with no runner available the host is still the final fallback (exit 4, host roles listed)",
          r2.returncode == 4 and '"host_roles"' in r2.stdout and "visual" in r2.stdout and "behavior" in r2.stdout,
          (r2.returncode, r2.stdout[-300:], r2.stderr[-300:]))


def t19_ui_no_choice_unchanged():
    proj = {"ui_reviewers": {"visual": ["claude", "codex"], "behavior": ["codex", "claude"]}}
    d, env, stubs, m = ui_world(proj)
    host, order = ui_order(d, env)
    check("[CP5-T19] the resolved order is the listed ui_reviewers order, as today",
          order == {"visual": ["claude", "codex"], "behavior": ["codex", "claude"]}, (host, order))
    r, out = run_ui_review(d, env, m)
    ran = ui_ran(stubs)
    check("[CP5-T19] each lens ran its first listed runner, as today",
          r.returncode == 0 and [n for n, _ in ran.get("visual", [])] == ["claude"]
          and [n for n, _ in ran.get("behavior", [])] == ["codex"], (r.returncode, ran, r.stderr[-300:]))
    check("[CP5-T19] no model flag was added", all("--model" not in a for rs in ran.values() for _, a in rs), ran)
    d2, env2, stubs2, m2 = ui_world(proj, runners=("not-a-reviewer",))
    r2, _ = run_ui_review(d2, env2, m2)
    check("[CP5-T19] with no runner the host is last, as today", r2.returncode == 4 and '"host_roles"' in r2.stdout,
          (r2.returncode, r2.stdout[-300:]))
    d3, env3, stubs3, m3 = ui_world({"agents": {}}, runners=("claude", "codex"))
    r3, _ = run_ui_review(d3, env3, m3)
    ran3 = ui_ran(stubs3)
    check("[CP5-T19] an empty agents setting changes nothing (the shipped lists, gemini being the host's own "
          "company: claude runs both lenses)",
          r3.returncode == 0 and [n for n, _ in ran3.get("visual", [])] == ["claude"]
          and [n for n, _ in ran3.get("behavior", [])] == ["claude"], (r3.returncode, ran3))


def t20_record_shows_the_model_that_ran():
    d, env, stubs, data = stub_review("stub/model-x")
    rv = reviewer_entry(data, "stub-both")
    out = Path(rv.get("output", d / "missing.json"))
    ext = run_review_script(d, env, "stub-both", out)
    calls = stubs.calls("stubcli", "review")
    rec = json.loads(out.read_text()) if out.exists() else {}
    check("[CP5-T20] the review script ran the stub reviewer", ext.returncode == 0 and len(calls) == 1,
          (ext.returncode, ext.stderr[-300:], stubs.calls()))
    check("[CP5-T20] the stub's argv contained model-x", bool(calls) and re.search(r"--model model-x( |$)", calls[0][2]),
          calls)
    check("[CP5-T20] the saved record names model-x", rec.get("model") == "model-x", sorted(rec))


def t21_model_stamp_is_signed():
    d, env, stubs, data = stub_review("stub/model-x")
    rv = reviewer_entry(data, "stub-both")
    out = Path(rv.get("output", d / "missing.json"))
    ext = run_review_script(d, env, "stub-both", out)
    original = out.read_text() if out.exists() else "{}"
    rec = json.loads(original)
    check("[CP5-T21] setup: a signed record that names model-x",
          ext.returncode == 0 and rec.get("model") == "model-x" and isinstance(rec.get("signature"), dict),
          (ext.returncode, sorted(rec)))
    out.write_text(json.dumps(dict(rec, model="model-y")))
    bad = cl.run(d, env, "adv-record", "CP1", "--reviews", str(out))
    why = cl.text(bad).lower()
    check("[CP5-T21] a hand-edited model field is refused, as changed after it was signed",
          bad.returncode != 0 and "signature" in why and ("does not match" in why or "changed after" in why),
          (bad.returncode, why[:300]))
    out.write_text(original)
    good = cl.run(d, env, "adv-record", "CP1", "--reviews", str(out))
    check("[CP5-T21] the untouched record is accepted", good.returncode == 0, (good.returncode, cl.text(good)[:300]))


def t22_provider_only_choice_records_no_invented_model():
    d, env, stubs, data = stub_review("stub")
    rv = reviewer_entry(data, "stub-both")
    out = Path(rv.get("output", d / "missing.json"))
    ext = run_review_script(d, env, "stub-both", out)
    calls = stubs.calls("stubcli", "review")
    rec = json.loads(out.read_text()) if out.exists() else {}
    check("[CP5-T22] the reviewer ran", ext.returncode == 0 and len(calls) == 1, (ext.returncode, ext.stderr[-300:]))
    check("[CP5-T22] the launched command has no model name in it (the provider's default, as today)",
          bool(calls) and re.search(r"--model\s*(-|$)", calls[0][2]) is not None, calls)
    check("[CP5-T22] the record shows what ran as today: reviewer, lens, vendor and an approval",
          rec.get("reviewer") == "stub-both" and rec.get("lens") == "both" and rec.get("vendor") == "stubco"
          and rec.get("verdict") == "APPROVE" and rec.get("checkpoint") == "CP1", rec)
    check("[CP5-T22] no model name is invented", not rec.get("model"), rec.get("model"))


def t23_global_applies_project_wins():
    d, env, _ = world({"agents": {"reviewer": "codex"}}, glob={"agents": {"reviewer": "gemini"}},
                      providers=[CODEX, GEMINI, PAID])
    got = resolve(d, env, "low")
    check("[CP5-T23] with the project value, codex is first (the project wins over the global gemini)",
          got.out is not None and got.ids == ["codex-both"], (got.ids, got.err[-300:]))
    cl.project_path(d).write_text(json.dumps({"providers": [CODEX, GEMINI, PAID]}))
    got2 = resolve(d, env, "low")
    check("[CP5-T23] with the project value removed, the global gemini is first (the shipped order puts codex first)",
          got2.out is not None and got2.ids == ["gemini-both"], (got2.ids, got2.err[-300:]))
    d0, env0, _ = world(providers=[CODEX, GEMINI, PAID])
    base = resolve(d0, env0, "low")
    check("[CP5-T23] and with neither, codex is first", base.ids == ["codex-both"], base.ids)


FINDING_MAJOR = json.dumps({"findings": [{"id": "X1", "severity": "major", "kind": "wrong-result", "file": "src/f.js",
                                          "line": 1, "rule": "f() must return 1 for every input", "description": "it does not",
                                          "fix": "return 1", "user_impact": "a user sees a wrong result in normal use"}]})


def _round_outcome(agents):
    """Two stub reviewers of different companies (alpha finds a major problem, beta approves), a normal-risk step with
    the standard two slots, recorded through adv-record. Returns (the round's reviewer ids, the recorded outcome)."""
    def cfg(st):
        out = {"providers": [stub_provider(st, "alphacli", "alpha", "acme-a"),
                             stub_provider(st, "betacli", "beta", "acme-b")]}
        if agents is not None:
            out["agents"] = agents
        return out
    d, env, stubs, data = approved_world(cfg, cp_extra={"risk": "normal"}, stub_names=("alphacli", "betacli"),
                                         replies={"alphacli": FINDING_MAJOR})
    ids = [x["id"] for x in data.get("reviewers", [])]
    paths = []
    for rv in data.get("reviewers", []):
        out = Path(rv["output"])
        run_review_script(d, env, rv["id"], out)
        paths.append(str(out))
    rec = cl.run(d, env, "adv-record", "CP1", "--reviews", *paths)
    st = json.loads((d / ".ratchet/runs/f/state.json").read_text())
    g = st["checkpoints"]["CP1"]["gates"]["adversarial"]
    last = (g.get("rounds") or [{}])[-1]
    return ids, {"exit": rec.returncode, "status": g.get("status"), "attempts": g.get("attempts"),
                 "findings": last.get("findings"), "deferred": last.get("deferred"),
                 "raw_findings": last.get("raw_findings"), "blocking_files": last.get("blocking_files"),
                 "claimed": sorted(v.get("claimed") for v in last.get("verdicts", [])),
                 "counts": sorted(v.get("findings") for v in last.get("verdicts", []))}


def t24_verdict_counting_unchanged():
    base_ids, base = _round_outcome(None)
    chosen_ids, chosen = _round_outcome({"reviewer": "beta"})
    check("[CP5-T24] setup: with no choice the round has two reviewers and a known blocked verdict (one blocking "
          "finding, one approval)",
          len(base_ids) == 2 and base["exit"] == 2 and base["status"] == "failed" and base["findings"] == 1
          and base["claimed"] == ["APPROVE", "CHANGES_REQUESTED"], (base_ids, base))
    check("[CP5-T24] with agents.reviewer set the round has the same number of reviewers",
          len(chosen_ids) == len(base_ids), (base_ids, chosen_ids))
    check("[CP5-T24] the verdict and blocking-severity outcome are identical", chosen == base, (base, chosen))


def t25_readme_lists_file():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("### Running the tests", 1)[-1]
    n = len(re.findall(r"^python3 tests/test_agents_reviewers\.py$", section, re.M))
    check("[CP5-T25] the README 'Running the tests' block lists python3 tests/test_agents_reviewers.py exactly once",
          n == 1, n)


def t26_inventory_registers_file():
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    src = Path(__file__).read_text()
    scans = re.search("spec_from_file" + "_location", src) is not None
    check("[CP5-T26] the test inventory registers tests/test_agents_reviewers.py", "test_agents_reviewers.py" in inv)
    check("[CP5-T26] the file is listed exactly when it loads a module by path (it does: ui_review.py)",
          scans and "test_agents_reviewers.py" in inv, f"scans={scans}")


def t27_models_view_matches_real_selection():
    cases = {}
    d, env, _ = world({"agents": {"reviewer": "codex/gpt-5.1"}})
    cases["usable"] = (d, env, "codex", False)
    d, env, _ = world({"agents": {"reviewer": "codex/gpt-5.1"}})
    write_bench(env, CODEX)
    cases["benched"] = (d, env, "codex", True)
    d, env, _ = world({"agents": {"reviewer": "codex/gpt-5.1"}, "provider_share": {"max": 1, "minutes": 15}})
    write_start(env, CODEX)
    cases["share-capped"] = (d, env, "codex", True)
    d, env, _ = world({"agents": {"reviewer": "zpay/opencode-go/glm-5.3"}})
    cases["unapproved paid"] = (d, env, "zpay", True)
    d, env, _ = world({"agents": {"reviewer": "codex/gpt-5.1"}})
    write_unavailable(d, [f"codex-{x}" for x in ("both", "standards", "correctness")])
    cases["marked unavailable"] = (d, env, "codex", True)
    for name, (d, env, prov, excluded) in cases.items():
        row, r = models_row(d, env)
        got = resolve(d, env, "low", share=True)
        first = (got.ids or [""])[0]
        real_is_chosen = first.startswith(prov + "-")
        shows = prov in s(row.get("effective")).lower()
        check(f"[CP5-T27] {name}: real resolution picks the chosen provider exactly when it is usable",
              got.out is not None and real_is_chosen == (not excluded), (got.ids, got.err[-300:]))
        check(f"[CP5-T27] {name}: the models view says reviewer was used exactly when reviewers would pick it",
              r.returncode == 0 and shows == real_is_chosen, (row, got.ids))
        if excluded:
            check(f"[CP5-T27] {name}: the view gives a plain fallback reason",
                  bool(row.get("reason")) and not shows, row)
        else:
            check(f"[CP5-T27] {name}: used, so the view gives no reason", not row.get("reason"), row)


def t28_ui_path_honors_paid_consent():
    paid = {"id": "codex", "preset": "codex", "cost": "per-use", "model": "gpt-5"}
    base = {"providers": [paid], "ui_reviewers": UI_LISTS_PAID}
    d, env, stubs, m = ui_world(base)
    r, out = run_ui_review(d, env, m)
    ran = ui_ran(stubs)
    check("[CP5-T28] unapproved paid provider listed first: the free runner is used for each lens, the paid one never ran",
          r.returncode == 0 and [n for n, _ in ran.get("visual", [])] == ["claude"]
          and [n for n, _ in ran.get("behavior", [])] == ["claude"] and not stubs.calls("codex"),
          (r.returncode, ran, r.stderr[-300:]))
    note = r.stderr.lower()
    check("[CP5-T28] the skip is explained plainly (codex, per use, needs the developer's OK)",
          "codex" in note and re.search(r"per[- ]use", note) is not None
          and re.search(r"\bok\b|approv|consent", note) is not None, r.stderr[-400:])
    row, rv = models_row(d, env, "ui-reviewer-visual")
    why = s(row.get("reason")).lower()
    check("[CP5-T28] the models view no longer says the paid-consent check is tracked to be closed",
          bool(row) and "tracked to be closed" not in why and "does not check paid consent" not in why, row)
    check("[CP5-T28] the models view shows the free runner and why codex was set aside",
          row.get("effective") == "claude" and "codex" in why and re.search(r"\bok\b|approv|consent", why) is not None, row)
    tr = cl.run(d, env, "models")
    check("[CP5-T28] the plain `models` text does not say it either", "tracked to be closed" not in tr.stdout, tr.stdout[-300:])
    ok_cfg = {"providers": [dict(paid, paid_ok_at="2026-01-01T00:00:00+00:00")], "ui_reviewers": UI_LISTS_PAID}
    d2, env2, stubs2, m2 = ui_world(ok_cfg)
    r2, _ = run_ui_review(d2, env2, m2)
    ran2 = ui_ran(stubs2)
    check("[CP5-T28] with the developer's recorded OK the paid provider may be used",
          r2.returncode == 0 and [n for n, _ in ran2.get("visual", [])] == ["codex"]
          and [n for n, _ in ran2.get("behavior", [])] == ["codex"], (r2.returncode, ran2, r2.stderr[-300:]))
    d3, env3, stubs3, m3 = ui_world({"providers": [paid], "ui_reviewers": {"visual": ["codex"], "behavior": ["codex"]}})
    r3, _ = run_ui_review(d3, env3, m3)
    check("[CP5-T28] no free runner after it: the host is used and the paid provider never ran",
          r3.returncode == 4 and '"host_roles"' in r3.stdout and not stubs3.calls("codex"),
          (r3.returncode, r3.stdout[-200:], stubs3.calls()))
    d4, env4, stubs4, m4 = ui_world({"providers": [paid], "ui_reviewers": UI_LISTS,
                                     "agents": {"visual-reviewer": "codex", "behavior-reviewer": "codex"}})
    r4, _ = run_ui_review(d4, env4, m4)
    ran4 = ui_ran(stubs4)
    check("[CP5-T28] a chosen (agents) paid provider with no OK is skipped the same way: the choice grants no consent",
          r4.returncode == 0 and not stubs4.calls("codex") and [n for n, _ in ran4.get("visual", [])] == ["claude"],
          (r4.returncode, ran4, r4.stderr[-300:]))



TESTS = [(f"CP5-T{i:02d}", fn) for i, fn in enumerate((
    t01_no_choice_same_reviewers, t02_empty_agents_changes_nothing, t03_chosen_provider_first,
    t04_provider_model_launches_that_model, t05_chosen_provider_leads_every_slot,
    t06_number_of_reviewers_never_changes, t07_paid_choice_needs_the_developers_reply,
    t08_paid_with_ok_ranks_after_flat, t09_choice_never_grants_paid_approval, t10_same_company_pick_not_independent,
    t11_same_company_only_never_silently_counted, t12_explicit_ids_keep_written_order,
    t13_checkpoint_pin_keeps_its_order, t14_unavailable_choice_falls_back_plainly, t15_share_cap_is_not_bypassed,
    t16_duplicate_company_model_lens_collapses, t17_unknown_reviewer_provider_refused_before_work,
    t18_ui_choices_lead_each_lens, t19_ui_no_choice_unchanged, t20_record_shows_the_model_that_ran,
    t21_model_stamp_is_signed, t22_provider_only_choice_records_no_invented_model, t23_global_applies_project_wins,
    t24_verdict_counting_unchanged, t25_readme_lists_file, t26_inventory_registers_file,
    t27_models_view_matches_real_selection, t28_ui_path_honors_paid_consent), 1)]


# ------------------------------------------------------------------------------------------------ round 1 fix cases
# Added by the CP5 adversarial-round-1 fixes (lane-added, not planned cases): each fails on the code before the fix.

FAIL_BAD_STUB = STUB.replace('if [ "$kind" = probe ]; then', 'case "$args" in *badmodel*) echo "error: unknown model badmodel" >&2; exit 1;; esac\nif [ "$kind" = probe ]; then', 1)


def x01_paid_ok_is_for_one_model_only():
    chosen = "opencode-go/glm-pricier"
    d, env, _ = world({"agents": {"reviewer": f"zpay/{chosen}"}}, providers=[PAID_OK])
    got = resolve(d, env, "low")
    one = got.slot(1)
    why = s((one.get("reasons") or {}).get("zpay-both"))
    check("[CP5-X01] a pay-per-use provider approved for another model is not picked for the chosen one",
          got.out is not None and not any(i.startswith("zpay") for i in got.ids), (got.ids, got.err[-300:]))
    check("[CP5-X01] it is named as skipped, with a reason that names the chosen model and says it has no OK",
          "zpay-both" in one.get("fallback_for", []) and chosen in why and re.search(r"\bOK\b", why) is not None, one)
    row, r = models_row(d, env)
    check("[CP5-X01] the models view does not show it as used and says no OK for the chosen model",
          "zpay" not in s(row.get("effective")).lower() and chosen in s(row.get("reason")), row)
    d2, env2, _ = world({"agents": {"reviewer": "zpay/opencode-go/glm-5.3"}}, providers=[PAID_OK])
    same = resolve(d2, env2, "low")
    check("[CP5-X01] choosing the exact model the developer approved still works",
          same.out is not None and same.ids == ["zpay-both"], (same.ids, same.err[-300:]))
    ui = {"id": "codex", "preset": "codex", "cost": "per-use", "model": "gpt-5", "paid_ok_at": "2026-01-01T00:00:00+00:00"}
    proj = {"providers": [ui], "ui_reviewers": UI_LISTS_PAID, "agents": {"visual-reviewer": "codex/gpt-6"}}
    d3, env3, stubs3, m3 = ui_world(proj)
    host, order = ui_order(d3, env3)
    check("[CP5-X01] the UI path skips the paid provider when a different model is chosen on it",
          order is not None and order.get("visual") == ["claude"] and order.get("behavior") == ["codex", "claude"],
          (host, order))
    r3, _ = run_ui_review(d3, env3, m3)
    check("[CP5-X01] the UI run printed why, and the paid provider ran only for the lens with no model choice",
          r3.returncode == 0 and "gpt-6" in r3.stderr and [n for n, _ in ui_ran(stubs3).get("visual", [])] == ["claude"],
          (r3.returncode, r3.stderr[-300:], ui_ran(stubs3)))
    row2, _ = models_row(d3, env3, "ui-reviewer-visual")
    check("[CP5-X01] the models view says the chosen UI model has no OK", "gpt-6" in s(row2.get("reason")), row2)


def x02_command_without_model_slot_is_not_stamped():
    def cfg(st):
        prov = stub_provider(st)
        prov["command"] = [str(st.bin / "stubcli"), "--fixed", "-"]   # no {model} anywhere
        return {"providers": [prov], "agents": {"reviewer": "stub/model-x"}}
    d, env, stubs, data = approved_world(cfg, stub_names=("stubcli",))
    rv = reviewer_entry(data, "stub-both")
    out = Path(rv.get("output", d / "missing.json"))
    ext = run_review_script(d, env, "stub-both", out)
    rec = json.loads(out.read_text()) if out.exists() else {}
    calls = stubs.calls("stubcli", "review")
    check("[CP5-X02] the reviewer ran", ext.returncode == 0 and len(calls) == 1, (ext.returncode, ext.stderr[-300:]))
    check("[CP5-X02] the command was launched as written (no model-x in it)", bool(calls) and "model-x" not in calls[0][2], calls)
    check("[CP5-X02] the signed record does not claim model-x ran", not rec.get("model"), rec.get("model"))
    note = cl.text(stubs.first_call).lower()
    check("[CP5-X02] `reviewers` says plainly the model could not be applied, naming {model}",
          "model-x" in note and "{model}" in note and "not applied" in note, note[-400:])
    row, r = models_row(d, env)
    check("[CP5-X02] the models view does not show model-x as used, and says why",
          "model-x" not in s(row.get("effective")) and "{model}" in s(row.get("reason")), row)
    d2, env2, stubs2, data2 = stub_review("stub/model-x")
    out2 = Path(reviewer_entry(data2, "stub-both").get("output", d2 / "missing.json"))
    run_review_script(d2, env2, "stub-both", out2)
    rec2 = json.loads(out2.read_text()) if out2.exists() else {}
    check("[CP5-X02] a command that does have {model} still launches and records the chosen model",
          rec2.get("model") == "model-x", rec2.get("model"))


def x03_pinned_and_listed_ids_keep_their_written_model():
    prov = {"id": "codex", "preset": "codex", "model": "gpt-5"}
    for name, cp_extra, proj in (("a checkpoint pin", {"reviewers": ["codex-both"]}, {}),
                                 ("a review_depth id", {}, {"review_depth": {"low": ["codex-both"]}})):
        cfg = dict({"providers": [dict(prov)], "agents": {"reviewer": "codex/gpt-5.1"}}, **proj)
        d, env, stubs, data = approved_world(cfg, cp_extra=cp_extra)
        rv = reviewer_entry(data, "codex-both")
        out = Path(rv.get("output", d / "missing.json"))
        ext = run_review_script(d, env, "codex-both", out)
        calls = stubs.calls("codex", "review")
        argv = calls[-1][2] if calls else ""
        rec = json.loads(out.read_text()) if out.exists() else {}
        check(f"[CP5-X03] {name}: the reviewer ran", ext.returncode == 0 and len(calls) == 1, (ext.returncode, ext.stderr[-300:]))
        check(f"[CP5-X03] {name}: the launched command keeps the written model gpt-5",
              re.search(r"(^| )-m gpt-5( |$)", argv) is not None and "gpt-5.1" not in argv, argv)
        check(f"[CP5-X03] {name}: the record does not name the unapplied model", rec.get("model") != "gpt-5.1", rec.get("model"))
        got = resolve(d, env, "low", pin=cp_extra.get("reviewers"))
        check(f"[CP5-X03] {name}: resolve_reviewers reports the written model too",
              got.out is not None and got.out[0].get("model") == "gpt-5", (got.out, got.err[-200:]))


def x04_failed_test_of_a_chosen_model_does_not_bench_the_default():
    def cfg(st):
        (st.bin / "codex").write_text(FAIL_BAD_STUB.replace("@LOG@", str(st.log)))
        return {"providers": [dict(CODEX), dict(GEMINI)], "agents": {"reviewer": "codex/badmodel"}}
    d, env, stubs, data = approved_world(cfg)
    ids = [x["id"] for x in data.get("reviewers", [])]
    check("[CP5-X04] setup: codex was tried with badmodel and failed its test request",
          any("badmodel" in c[2] for c in stubs.calls("codex")), stubs.calls())
    check("[CP5-X04] this project falls back to gemini for that round", ids == ["gemini-both"], ids)
    hp = Path(env["RATCHET_HOME"]) / "provider-health.json"
    shared = json.loads(hp.read_text()) if hp.exists() else {}
    check("[CP5-X04] the shared record holds no bench for codex's own model",
          not (shared.get(health_key(CODEX)) or {}).get("bench"), shared)
    d2, env2, _ = world(providers=[dict(CODEX)])
    env2["RATCHET_HOME"] = env["RATCHET_HOME"]    # the same machine: another project, no agents setting
    other = resolve(d2, env2, "low")
    check("[CP5-X04] another project with no setting still gets codex", other.out is not None and other.ids == ["codex-both"],
          (other.ids, other.err[-300:]))


def x05_provider_not_set_up_is_said_plainly():
    d, env, stubs, data = approved_world({"providers": [dict(CODEX)], "agents": {"reviewer": "gemini"}},
                                         stub_names=("codex",))
    note = cl.text(stubs.first_call).lower()
    check("[CP5-X05] `reviewers` says the chosen provider is not set up in this project",
          "agents.reviewer" in note and "gemini" in note and "not set up in this project" in note, note[-400:])
    row, r = models_row(d, env)
    why = s(row.get("reason"))
    check("[CP5-X05] the models view says not set up, not 'is a model'",
          "not set up in this project" in why and "is a model" not in why, row)
    proj = {"ui_reviewers": UI_LISTS, "agents": {"visual-reviewer": "gemini", "behavior-reviewer": "copilot"}}
    d2, env2, stubs2, m2 = ui_world(proj)
    r2, _ = run_ui_review(d2, env2, m2)
    err = r2.stderr.lower()
    check("[CP5-X05] the UI run says why a chosen lens provider was dropped (same company, not a UI runner)",
          r2.returncode == 0 and "agents.visual-reviewer" in err and "same company" in err
          and "agents.behavior-reviewer" in err and "not a ui review provider" in err, r2.stderr[-500:])


def x06_inventory_entry_matches_what_the_scanner_finds():
    inv = (ROOT / "tests/fixtures/modularize_cp1/test-inventory.md").read_text()
    kind1 = inv.split("## Kind 1", 1)[-1].split("## Kind 2", 1)[0]
    bullets = re.findall(r"(?m)^- `([^`]+)`", kind1)
    stated = re.search(r"\): (\d+) files", kind1)
    module_load = re.compile("spec_from_file" + "_location")
    source_text = re.compile("ENGINE\\.read_" + "text|ast\\.pa" + "rse|ast\\.wa" + "lk|getsou" + "rce")
    mine = Path(__file__).read_text()
    check("[CP5-X06] the entry sits under the 'Kind 1' (module load only) heading", "test_agents_reviewers.py" in bullets,
          bullets[-3:])
    check("[CP5-X06] the Kind 1 heading's file count equals the entries listed under it",
          stated is not None and int(stated.group(1)) == len(bullets), (stated and stated.group(1), len(bullets)))
    check("[CP5-X06] the scanner classifies this file as module-load only (matches that pattern, not the other)",
          module_load.search(mine) is not None and source_text.search(mine) is None)


def x07_selection_changes_no_config_file():
    glob = {"agents": {"reviewer": "zpay"}}
    d, env, _ = world({"agents": {"reviewer": "zpay"}}, glob=glob)
    files = (cl.project_path(d), cl.global_path(env))
    before = [p.read_text() for p in files]
    got = resolve(d, env, "normal")
    after = [p.read_text() for p in files]
    check("[CP5-X07] the selection ran", got.out is not None and got.ids == ["gemini-standards", "codex-correctness"], got.ids)
    check("[CP5-X07] the project and the machine-wide config are byte for byte as before", before == after, (before, after))
    check("[CP5-X07] neither holds a paid_ok_at afterwards", all("paid_ok_at" not in t for t in after), after)


TESTS += [("CP5-X%02d" % i, fn) for i, fn in enumerate((
    x01_paid_ok_is_for_one_model_only, x02_command_without_model_slot_is_not_stamped,
    x03_pinned_and_listed_ids_keep_their_written_model, x04_failed_test_of_a_chosen_model_does_not_bench_the_default,
    x05_provider_not_set_up_is_said_plainly, x06_inventory_entry_matches_what_the_scanner_finds,
    x07_selection_changes_no_config_file), 1)]


# ---- round-2 fixes: the choice is applied per slot only, so the round, the models view and the launch agree

def probe_models(stubs, name="codex"):
    """The argv text of every test request the stub `name` got."""
    return [c[2] for c in stubs.calls(name, "probe")]


def unavailable_path(d):
    return Path(d) / ".rat" "chet" / "unavailable-reviewers.json"


def unavailable_ids(d):
    p = unavailable_path(d)
    return sorted(json.loads(p.read_text())) if p.exists() else []


def x08_pinned_and_listed_slots_keep_written_model_through_reviewers():
    prov = {"id": "codex", "preset": "codex", "model": "gpt-5"}
    for name, cp_extra, proj in (("a checkpoint pin", {"reviewers": ["codex-both"]}, {}),
                                 ("a review_depth id", {}, {"review_depth": {"low": ["codex-both"]}})):
        for chosen, bad in (("gpt-5.1", False), ("badmodel", True)):
            def cfg(st, chosen=chosen, bad=bad, proj=proj):
                if bad:
                    (st.bin / "codex").write_text(FAIL_BAD_STUB.replace("@LOG@", str(st.log)))
                return dict({"providers": [dict(prov), dict(GEMINI)], "agents": {"reviewer": f"codex/{chosen}"}}, **proj)
            d, env, stubs, data = approved_world(cfg, cp_extra=cp_extra)
            tag = f"{name}, chosen {chosen}"
            rows = data.get("reviewers", [])
            check(f"[CP5-X08] {tag}: the pinned reviewer still reviews (not pushed to another company)",
                  [x.get("id") for x in rows] == ["codex-both"], [x.get("id") for x in rows])
            check(f"[CP5-X08] {tag}: `reviewers` reports the written model gpt-5, the one the launch runs",
                  bool(rows) and rows[0].get("model") == "gpt-5", rows)
            sent = probe_models(stubs)
            check(f"[CP5-X08] {tag}: the test request used the written model and never the chosen one",
                  any(re.search(r"-m gpt-5( |$)", a) for a in sent) and not any(chosen in a for a in sent), sent)
            check(f"[CP5-X08] {tag}: nothing is marked unavailable in the project", unavailable_ids(d) == [], unavailable_ids(d))
    d, env, stubs, data = approved_world(
        {"providers": [dict(prov)], "agents": {"reviewer": "codex/gpt-5.1"}, "review_depth": {"low": ["codex-both"]}})
    row, _ = models_row(d, env)
    got = resolve(d, env, "low")
    check("[CP5-X08] the models view names the model the resolution really reports (gpt-5), not the chosen one",
          got.out is not None and got.out[0]["model"] == "gpt-5" and "gpt-5.1" not in s(row.get("effective"))
          and "gpt-5" in s(row.get("effective")), (row, got.out))
    check("[CP5-X08] the models view says the choice was not applied because review_depth lists ids",
          "review_depth lists reviewer ids" in s(row.get("reason")), row)
    d2, env2, stubs2, data2 = approved_world({"providers": [dict(prov), dict(GEMINI)], "agents": {"reviewer": "codex/gpt-5.1"}})
    rows2 = data2.get("reviewers", [])
    sent2 = probe_models(stubs2)
    check("[CP5-X08] an any-<lens> slot still takes the chosen model: reported, tested and shown in the models view",
          bool(rows2) and rows2[0].get("id") == "codex-both" and rows2[0].get("model") == "gpt-5.1"
          and any("gpt-5.1" in a for a in sent2) and "gpt-5.1" in s(models_row(d2, env2)[0].get("effective")),
          (rows2, sent2))


def x09_paid_pinned_slot_approved_for_its_own_model_is_not_refused():
    for name, cp_extra, proj in (("a checkpoint pin", {"reviewers": ["zpay-both"]}, {}),
                                 ("a review_depth id", {}, {"review_depth": {"low": ["zpay-both"]}})):
        d, env, stubs, data = approved_world(
            dict({"providers": [dict(PAID_OK), dict(GEMINI)], "agents": {"reviewer": "zpay/opencode-go/glm-pricier"}}, **proj),
            cp_extra=cp_extra, stub_names=("codex", "gemini", "opencode"))
        rows = data.get("reviewers", [])
        check(f"[CP5-X09] {name}: the approved pay-per-use reviewer is used, not refused for the chosen model",
              [x.get("id") for x in rows] == ["zpay-both"], [x.get("id") for x in rows])
        check(f"[CP5-X09] {name}: it is reported on the model that has the OK",
              bool(rows) and rows[0].get("model") == "opencode-go/glm-5.3", rows)
        check(f"[CP5-X09] {name}: the chosen model is never sent a request",
              not any("glm-pricier" in a for a in probe_models(stubs, "opencode")), probe_models(stubs, "opencode"))
    row, _ = models_row(d, env)
    check("[CP5-X09] the models view does not call the listed, approved pay-per-use reviewer unapproved",
          "costs money" not in s(row.get("reason")) and "glm-pricier" not in s(row.get("effective")), row)


def x10_failed_chosen_model_test_ends_with_the_choice():
    def cfg(st):
        (st.bin / "codex").write_text(FAIL_BAD_STUB.replace("@LOG@", str(st.log)))
        return {"providers": [dict(CODEX)], "agents": {"reviewer": "codex/badmodel"}}
    d, env, stubs, data = approved_world(cfg)
    check("[CP5-X10] setup: the round left codex for the host after the failed test of badmodel",
          [x.get("id") for x in data.get("reviewers", [])] != ["codex-both"] and unavailable_ids(d) != [],
          ([x.get("id") for x in data.get("reviewers", [])], unavailable_ids(d)))
    marks = json.loads(unavailable_path(d).read_text())
    check("[CP5-X10] the mark says which model it is about", all(v.get("model") == "badmodel" for v in marks.values()), marks)
    same = resolve(d, env, "low")
    check("[CP5-X10] while the same bad choice stays set, codex is still left out", "codex-both" not in same.ids, same.ids)
    row_set, _ = models_row(d, env)
    check("[CP5-X10] while the setting is still there, the models view names the failed model badmodel",
          "badmodel" in s(row_set), row_set)
    proj = json.loads(cl.project_path(d).read_text())
    cl.project_path(d).write_text(json.dumps(dict(proj, agents={"reviewer": "codex/gpt-5.1"})))
    now = resolve(d, env, "low")
    check("[CP5-X10] correcting the model brings codex back at once", now.ids == ["codex-both"], (now.ids, now.err[-200:]))
    gone = dict(proj)
    gone.pop("agents")
    cl.project_path(d).write_text(json.dumps(gone))
    back = resolve(d, env, "low")
    check("[CP5-X10] removing the setting brings codex back at once, as before any choice", back.ids == ["codex-both"],
          (back.ids, back.err[-200:]))
    row, _ = models_row(d, env)
    check("[CP5-X10] the models view no longer blames a model that is not configured", "badmodel" not in s(row), row)


def x11_choice_keeps_cross_company_preference_and_counts():
    d, env, _ = world({"agents": {"reviewer": "codex/gpt-5.1"}}, providers=[GEMINI, CODEX])
    got = resolve(d, env, "normal")
    vendors = {x["id"]: x["vendor"] for x in got.out or []}
    check("[CP5-X11] a normal step still gets two reviewers, from two different companies",
          got.out is not None and len(got.out) == 2 and len(set(vendors.values())) == 2, got.out)
    check("[CP5-X11] the chosen provider leads and carries the chosen model; the other company's keeps its own",
          got.out is not None and got.out[0]["id"].startswith("codex-") and got.out[0]["model"] == "gpt-5.1"
          and not str(got.out[1].get("model") or "").startswith("gpt-5.1"), got.out)
    cfg = {"providers": [dict(GEMINI), dict(CODEX)], "agents": {"reviewer": "codex/gpt-5.1"},
           "review_depth": {"low": ["gemini-both", "codex-both"]}}
    d2, env2, stubs2, data2 = approved_world(cfg)
    ids = [x.get("id") for x in data2.get("reviewers", [])]
    check("[CP5-X11] listed ids keep their count, order and written models through `reviewers`",
          ids == ["gemini-both", "codex-both"] and data2["reviewers"][1].get("model") != "gpt-5.1", data2.get("reviewers"))


TESTS += [("CP5-X%02d" % i, fn) for i, fn in enumerate((
    x08_pinned_and_listed_slots_keep_written_model_through_reviewers,
    x09_paid_pinned_slot_approved_for_its_own_model_is_not_refused,
    x10_failed_chosen_model_test_ends_with_the_choice,
    x11_choice_keeps_cross_company_preference_and_counts), 8)]


# ---- round-3 fixes (lane-added): a failed chosen-model test through `reviewers`, one slot basis for a small-fix round

def reopen_round(d, env, tag):
    """The code changes (a new line in src/f.js), so `reviewers` opens a new round; returns (its JSON, the result)."""
    with open(Path(d) / "src" / "f.js", "a") as f:
        f.write(f"// {tag}\n")
    r = cl.run(d, env, "reviewers", "CP1", "--json")
    return ac.jload(r) or {}, r


def round_ids(data):
    return [x.get("id") for x in data.get("reviewers", [])]


def engine_json(d, env, body):
    """Run `body(m, ctx, cfg)` (returns a JSON-able value) inside the project, in-process, and return what it gave."""
    m = cl.engine()
    with th.InProcessCall(m, d, env, cl.RESET_GLOBALS, cl.LOCK_WAIT_CAP) as r:
        ctx = m.Ctx(m.find_root())
        print(json.dumps(body(m, ctx, ctx.config())))
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


PAID_CODEX_OK = {"id": "codex", "preset": "codex", "model": "gpt-5", "cost": "per-use",
                 "paid_ok_at": "2026-01-01T00:00:00+00:00"}


def x13_small_fix_round_uses_the_slots_it_was_resolved_from():
    """A small fix after approval is resolved from review_depth['low']; the choice, the paid check, the rows and the
    launch must read those same slots (not the step's own), or a listed paid id could launch on the chosen model."""
    proj = {"providers": [dict(PAID_CODEX_OK)], "agents": {"reviewer": "codex/gpt-5-mini"},
            "review_depth": {"low": ["codex-both"], "normal": ["any-standards", "any-correctness"]}}
    d, env, _ = world(proj)

    def body(m, ctx, cfg):
        cp = {"id": "CP1", "risk": "normal"}
        low = m._small_fix_reviewers(ctx, cfg, cp)
        rnd = {"small_fix": True, "kind": m.FIX_AFTER_APPROVAL, "resolved": low}
        lc = m.launch_cfg(cfg, cp, rnd, "codex-both")
        block = {"small_fix": True, "kind": m.FIX_AFTER_BLOCK, "resolved": [{"id": "codex-correctness", "slot": 2}]}
        return {"low": low, "applies": m.choice_applies_to(cfg, cp, rnd, "codex-both"),
                "model": m._roster_spec(lc, "codex-both").get("model"),
                "unapproved": m.per_use_unapproved(m.provider_by_id(lc, "codex")),
                "notes": m.reviewer_choice_notes(cfg, cp, rnd),
                "block_applies": m.choice_applies_to(cfg, cp, block, "codex-correctness")}
    got = engine_json(d, env, body)
    check("[CP5-X13] setup: the small fix is resolved to the listed, approved paid reviewer on its written model",
          bool(got) and [x["id"] for x in got["low"]] == ["codex-both"] and got["low"][0]["model"] == "gpt-5", got)
    check("[CP5-X13] the choice is not applied to that listed id: it launches the approved gpt-5, never gpt-5-mini",
          bool(got) and got["applies"] is False and got["model"] == "gpt-5" and got["unapproved"] is False, got)
    check("[CP5-X13] the notes say plainly that the choice was not applied to the small fix's listed slot",
          bool(got) and any("not applied" in n and "review_depth lists reviewer ids" in n for n in got["notes"]), got)
    check("[CP5-X13] a fix round after a block still follows the step's own slots (an any-<lens> slot takes the choice)",
          bool(got) and got["block_applies"] is True, got)
    # the reverse layout: low takes the choice, the step lists ids; the round resolved from low must launch the chosen model
    proj2 = {"providers": [dict(CODEX)], "agents": {"reviewer": "codex/gpt-5-mini"},
             "review_depth": {"low": ["any-both"], "normal": ["codex-standards", "codex-correctness"]}}
    d2, env2, _ = world(proj2)

    def body2(m, ctx, cfg):
        cp = {"id": "CP1", "risk": "normal"}
        low = m._small_fix_reviewers(ctx, cfg, cp)
        rnd = {"small_fix": True, "kind": m.FIX_AFTER_APPROVAL, "resolved": low}
        lc = m.launch_cfg(cfg, cp, rnd, low[0]["id"])
        return {"low": low, "applies": m.choice_applies_to(cfg, cp, rnd, low[0]["id"]),
                "model": m._roster_spec(lc, low[0]["id"]).get("model")}
    got2 = engine_json(d2, env2, body2)
    check("[CP5-X13] reverse layout: the row, the launch and the pick agree on the chosen model for the small fix",
          bool(got2) and got2["applies"] is True and got2["model"] == "gpt-5-mini" == got2["low"][0]["model"], got2)


LAUNCH_AS_IF_CHOSEN = """
import sys
sys.path.insert(0, sys.argv[1])
import external_review as er
real = er.load_engine
def patched():
    eng = real()
    eng.choice_applies_to = lambda *a, **k: True    # a round whose slot basis disagrees: the choice lands at the launch
    return eng
er.load_engine = patched
sys.argv = ["external_review.py"] + sys.argv[2:]
er.main()
"""


def x14_launch_refuses_a_paid_model_without_an_ok():
    """external_review.py never launches a per-use provider on a model with no OK, whatever the round says: even when
    the choice lands on a reviewer the round did not expect it for (simulated by forcing choice_applies_to)."""
    cfgd = {"providers": [dict(PAID_OK)], "agents": {"reviewer": "zpay/opencode-go/glm-pricier"}}
    d, env, stubs, data = approved_world(cfgd, cp_extra={"reviewers": ["zpay-both"]},
                                         stub_names=("codex", "gemini", "opencode"))
    check("[CP5-X14] setup: the pinned, approved paid reviewer is the round, on its written model",
          round_ids(data) == ["zpay-both"] and reviewer_entry(data, "zpay-both").get("model") == "opencode-go/glm-5.3",
          data.get("reviewers"))
    out = Path(d) / "out.json"
    runner = Path(_tmp.mkdtemp(prefix="ratchet-agrev-run-")) / "run.py"
    runner.write_text(LAUNCH_AS_IF_CHOSEN)
    r = subprocess.run([sys.executable, str(runner), str(EXT.parent), "--reviewer", "zpay-both", "--cp", "CP1",
                        "--out", str(out), "--root", str(d)], cwd=d, capture_output=True, text=True, env=env, timeout=240)
    check("[CP5-X14] the launch is refused plainly (exit 3), naming the missing OK for the chosen model",
          r.returncode == 3 and "glm-pricier" in r.stderr and "costs money per use" in r.stderr,
          (r.returncode, r.stderr[-300:]))
    check("[CP5-X14] no review request reached the paid provider's CLI, and no review was written",
          [c for c in stubs.calls("opencode") if c[1] == "review"] == [] and not out.exists(),
          (stubs.calls("opencode"), out.exists()))
    ok = run_review_script(d, env, "zpay-both", out)
    check("[CP5-X14] the same reviewer, launched normally on its approved model, is not refused",
          "costs money per use" not in ok.stderr and any(c[1] == "review" for c in stubs.calls("opencode")),
          (ok.returncode, ok.stderr[-300:], stubs.calls("opencode")))


def x15_same_round_refill_checks_the_model_the_slot_launches():
    """A reviewer marked unavailable for the chosen model is replaced in the open round (the refill reads the launch
    config), and a mark about another model leaves it alone."""
    for model, replaced in (("gpt-5.1", True), ("gpt-4o", False)):
        d, env, stubs, data = approved_world({"providers": [dict(CODEX), dict(GEMINI)], "agents": {"reviewer": "codex/gpt-5.1"}})
        check(f"[CP5-X15] setup: the round opens on codex with the chosen model (mark for {model})",
              round_ids(data) == ["codex-both"] and reviewer_entry(data, "codex-both").get("model") == "gpt-5.1", data.get("reviewers"))
        unavailable_path(d).write_text(json.dumps({"codex-both": {"reason": "test request failed", "since": LONG_AGO,
                                                                  "until": FAR, "model": model}}))
        r = cl.run(d, env, "reviewers", "CP1", "--json")
        again = ac.jload(r) or {}
        check(f"[CP5-X15] the open round {'hands the slot on' if replaced else 'keeps codex'} when the mark is for {model}",
              (round_ids(again) == ["gemini-both"]) if replaced else (round_ids(again) == ["codex-both"]),
              (round_ids(again), cl.text(r)[-300:]))


def x16_small_fix_refill_checks_the_model_the_slot_launches():
    """The one reviewer of a small fix after approval is replaced when it is marked unavailable for the chosen model."""
    d, env, stubs, data = approved_world({"providers": [dict(CODEX), dict(GEMINI)], "agents": {"reviewer": "codex/gpt-5.1"}})
    out = Path(data["output_dir"]) / "codex-both.json"
    run = run_review_script(d, env, "codex-both", out)
    rec = cl.run(d, env, "adv-record", "CP1", "--reviews", str(out))
    check("[CP5-X16] setup: round 1 was reviewed by codex and recorded as approved",
          run.returncode == 0 and rec.returncode == 0, (run.stderr[-200:], cl.text(rec)[-300:]))
    small, r = reopen_round(d, env, "small fix")
    check("[CP5-X16] setup: the next round is a small fix with codex on the chosen model",
          small.get("small_fix") is True and round_ids(small) == ["codex-both"] and
          reviewer_entry(small, "codex-both").get("model") == "gpt-5.1", (small.get("reviewers"), cl.text(r)[-300:]))
    unavailable_path(d).write_text(json.dumps({"codex-both": {"reason": "test request failed", "since": LONG_AGO,
                                                              "until": FAR, "model": "gpt-5.1"}}))
    r = cl.run(d, env, "reviewers", "CP1", "--json")
    again = ac.jload(r) or {}
    check("[CP5-X16] the small fix hands its slot on when the mark is for the chosen model",
          round_ids(again) == ["gemini-both"], (round_ids(again), cl.text(r)[-300:]))


TESTS += [("CP5-X13", x13_small_fix_round_uses_the_slots_it_was_resolved_from),
          ("CP5-X14", x14_launch_refuses_a_paid_model_without_an_ok),
          ("CP5-X15", x15_same_round_refill_checks_the_model_the_slot_launches),
          ("CP5-X16", x16_small_fix_refill_checks_the_model_the_slot_launches)]


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
