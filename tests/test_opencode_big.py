#!/usr/bin/env python3
"""
CP7 tests: big reviews work on OpenCode providers.   python3 tests/test_opencode_big.py

An OpenCode reviewer (GLM, Kimi) is handed the whole review prompt on stdin, however large it is, with a short message,
`--format json` and a capped thinking level (`--variant low` unless the provider sets another value, or "none" to turn
the cap off). Ratchet reads the reviewer's answer from the text events of the JSON event stream only: a reasoning or
tool event that happens to hold findings never becomes the review, an exit 0 with no answer text and a "length" finish
(the model ran out of output room, even when what it wrote looks complete) are failed attempts for that round only
(never an approval, never a bench), and an error event that names a usage limit still benches the provider for every
lane. A prompt too large for a command line (a CLI that takes the prompt as an argument: cursor, copilot, omp, pi)
never benches or marks the provider; only that slot moves on for the round and the start is given back.

Every case builds throwaway git projects (tests/test_gates.py's project(), taken to the adversarial step of a
risk-labelled step, neutral names such as "alpha") with their own per-user folder (RATCHET_HOME, shared by two lanes
when a case needs two) and their own private PATH holding only symlinks to python3, git, node, sh, env, cat, dirname
and basename, plus the fake agent CLIs. The decisive fake is `opencode`: a Python script that refuses any single
argument over 131072 bytes, reads every word after -f as a file name (a word that is not an existing file is a "file
not found" error, exit 1), reads stdin when it is not a terminal and joins it to the message as message + newline +
stdin (the real CLI's rule), and prints a scripted event stream in the shape of `opencode run --format json` (the
shape was read, without sending any request, from the bundled source of opencode 1.18.27: every event is one JSON
line {"type", "timestamp", "sessionID", "part": {...}} with type step_start, text, reasoning, tool_use or step_finish
(its part carries "reason": "stop" or "length"), or {"type": "error", "error": {"name", "data": {"message"}}}). The
reply of each model is scripted by files beside it: <tag>.script.json (a JSON list of events, for a real review),
<tag>.probe.json (the same, for the tiny test request), <tag>.exit, <tag>.stderr and <tag>.plain (print this plain
text and ignore --format json); the tag is glm, kimi, hi, off or def from the -m model. Each call is logged as one JSON
line to $RATCHET_FAKE_LOGS/calls.log. The other fakes (cursor-agent, copilot, omp, pi, codex) are one small shell
script that logs "<kind> <longest argument> <stdin bytes>" to $RATCHET_FAKE_LOGS/<name>.log and answers cleanly.
The agent's side is played through the public commands only: `reviewers CP1 --json`, external_review.py (also with
--dry-run, to learn the prompt's size), `reviewer-status`, `providers --add` and `providers --check`. No real provider
is called, there is no network and no money is spent.

Each case is its own function, registered with @case("CP7-Txx") on the line directly above it and run from main()
inside try/except, so a crash is a failed case, never a silent drop. Writes a per-test JSON report to
RATCHET_TEST_REPORT when set: {"cases": [{"id": "CP7-T01", "status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                     # noqa: E402
import test_gates as g          # noqa: E402  (project, approve_plan, to_adversarial, rx, ENV)

EXT = ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py"
UTC = datetime.timezone.utc

PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
_CUR_CASE: str | None = None
TESTS: list = []
ORIG_HOME = g.ENV["RATCHET_HOME"]


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    label = f"[{_CUR_CASE}] {name}" if _CUR_CASE else name
    if _CUR_CASE:
        CASE_STATUS[_CUR_CASE] = CASE_STATUS.get(_CUR_CASE, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(_CUR_CASE, []).append(name)
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"   {str(detail)[:400]}"))


class Stop(Exception):
    """A set-up step of the case failed (already reported as a failed check): nothing later can mean anything."""


def need(name, cond, detail=""):
    check(name, cond, detail)
    if not cond:
        raise Stop(name)


def case(cid):
    def deco(fn):
        TESTS.append((cid, fn))
        return fn
    return deco


# ------------------------------------------------------------------------------------------------ fixtures

# The fake `opencode`. __PY__ is replaced by the private PATH's python3 symlink.
FAKE_OPENCODE = r'''#!__PY__
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
LIMIT = 131072                      # the system's limit for one argument: refused like a real exec would
argv = sys.argv[1:]


def enc(s):
    return s.encode("utf-8", "surrogateescape")


longest = max([len(enc(a)) for a in argv] or [0])
if longest > LIMIT:
    sys.stderr.write("opencode: Argument list too long\n")
    sys.exit(126)

model, fmt, variant = None, "default", None
files, words, kept = [], [], []
seen_run = False
i = 0
while i < len(argv):
    a = argv[i]
    if a in ("-m", "--model") and i + 1 < len(argv):
        model = argv[i + 1]; kept += [a, argv[i + 1]]; i += 2
    elif a == "--format" and i + 1 < len(argv):
        fmt = argv[i + 1]; kept += [a, argv[i + 1]]; i += 2
    elif a == "--variant" and i + 1 < len(argv):
        variant = argv[i + 1]; kept += [a, argv[i + 1]]; i += 2
    elif a in ("-f", "--file"):         # greedy, like the real CLI: every following word is a file
        kept.append(a); files = argv[i + 1:]; kept += files; i = len(argv)
    elif a.startswith("-"):
        kept.append(a); i += 1
    elif a == "run" and not seen_run:
        seen_run = True; kept.append(a); i += 1
    else:
        words.append(a); i += 1

for f in files:
    if not os.path.isfile(f):
        sys.stderr.write("Error: File not found: %s\n" % f)
        sys.exit(1)

data = b"" if sys.stdin.isatty() else sys.stdin.buffer.read()
message = " ".join(words)
stdin_text = data.decode("utf-8", "replace")
full = message + ("\n" + stdin_text if stdin_text else "")
kind = "probe" if "connectivity check" in full else "review"
m = model or ""
tag = "glm" if "glm" in m else "kimi" if "kimi" in m else next((t for t in ("hi", "off", "def") if m.endswith("-" + t)), "def")

logs = os.environ.get("RATCHET_FAKE_LOGS")
if logs:
    with open(os.path.join(logs, "calls.log"), "a") as f:
        f.write(json.dumps({"kind": kind, "tag": tag, "model": model, "format": fmt, "variant": variant, "argv": kept,
                            "longest": longest, "msg_len": len(enc(message)),
                            "msg_has_check": "connectivity check" in message, "stdin_len": len(data),
                            "stdin_tail": stdin_text[-120:], "files": files}) + "\n")
    if kind == "review":
        with open(os.path.join(logs, "last-stdin.txt"), "wb") as f:
            f.write(data)


def beside(name):
    p = os.path.join(here, "%s.%s" % (tag, name))
    return p if os.path.exists(p) else None


def load_events():
    p = beside("probe.json" if kind == "probe" else "script.json")
    if p:
        return json.load(open(p))
    if kind == "probe":
        text = "RATCHET-PROBE-OK"
    else:
        text = json.dumps({"reviewer": "x", "verdict": "APPROVE", "findings": []})
    return [{"type": "step_start", "part": {"type": "step-start"}}, {"type": "text", "part": {"type": "text", "text": text}},
            {"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}}]


def fill(v):
    if isinstance(v, str):
        return v.replace("{{REQUEST}}", full)
    if isinstance(v, list):
        return [fill(x) for x in v]
    if isinstance(v, dict):
        return {k: fill(x) for k, x in v.items()}
    return v


events = fill(load_events())
exit_code = 1 if any(e.get("type") == "error" for e in events) else 0
review_only = kind == "review"          # the test request is answered by probe.json alone
p = beside("exit") if review_only else None
if p:
    exit_code = int(open(p).read().strip())
p = beside("stderr") if review_only else None
if p:
    sys.stderr.write(open(p).read())

plain = beside("plain") if review_only else None
if plain:
    sys.stdout.write(open(plain).read() + "\n")
    sys.exit(exit_code)

for n, e in enumerate(events):
    out = {"type": e["type"], "timestamp": 1760000000000 + n, "sessionID": "ses_fake"}
    if "part" in e:
        part = dict(e["part"])
        part.setdefault("id", "prt_%d" % n); part.setdefault("sessionID", "ses_fake"); part.setdefault("messageID", "msg_fake")
        if part.get("type") in ("text", "reasoning"):
            part.setdefault("time", {"start": 1760000000000, "end": 1760000000001})
        out["part"] = part
    if "error" in e:
        out["error"] = e["error"]
    if fmt == "json":
        sys.stdout.write(json.dumps(out) + "\n")
    elif e["type"] == "text" and e["part"].get("text", "").strip():
        sys.stdout.write(e["part"]["text"].strip() + "\n")
    elif e["type"] == "error":
        err = e["error"]
        sys.stderr.write(str((err.get("data") or {}).get("message") or err.get("name")) + "\n")
sys.exit(exit_code)
'''

# The other fakes: log "<kind> <longest argument> <stdin bytes>", answer the test request with the token and a review
# with no findings (codex also writes its answer to --output-last-message).
FAKE_SH = r"""#!/bin/sh
me=$(basename "$0")
out=""
all="$*"
longest=0
for a in "$@"; do
  n=${#a}
  if [ "$n" -gt "$longest" ]; then longest=$n; fi
done
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
input=""
if [ ! -t 0 ]; then input=$(cat); fi
kind=review
case "$input" in *"connectivity check"*) kind=probe;; esac
if [ -n "$RATCHET_FAKE_LOGS" ]; then printf '%s %s %s\n' "$kind" "$longest" "${#input}" >> "$RATCHET_FAKE_LOGS/$me.log"; fi
if [ "$kind" = probe ]; then reply="RATCHET-PROBE-OK"; else reply='{"findings": []}'; fi
if [ -n "$out" ]; then printf '%s' "$reply" > "$out"; fi
printf '%s\n' "$reply"
"""

# The fake arg-delivery programs probe differently: their test request arrives as an argument, not on stdin.
FAKE_SH_ARG = FAKE_SH.replace('case "$input" in', 'case "$input $all" in')

GLM = {"id": "opencode-glm", "preset": "opencode", "model": "opencode-go/glm-5.3", "cost": "flat"}
KIMI = {"id": "opencode-kimi", "preset": "opencode", "model": "opencode-go/kimi-k2.6", "cost": "flat"}
CODEX = {"id": "codex", "preset": "codex"}
ARG_PROVIDERS = {          # fake program, provider (company set so they count as other-company)
    "cursor": ("cursor-agent", {"id": "cursor", "preset": "cursor", "company": "xai"}),
    "copilot": ("copilot", {"id": "copilot", "preset": "copilot", "company": "github"}),
    "omp": ("omp", {"id": "omp", "preset": "omp", "company": "mistral"}),
    "pi": ("pi", {"id": "pi", "preset": "pi", "company": "meta"}),
}
GLM_KEY = "opencode:opencode-go/glm-5.3"
KIMI_KEY = "opencode:opencode-go/kimi-k2.6"

CLEAN = json.dumps({"reviewer": "opencode-glm-both", "verdict": "APPROVE", "findings": []})
FINDING_F1 = {"id": "F1", "severity": "major", "kind": "constant-result", "file": "src/f.js", "line": 3,
              "rule": "f() must return the caller's value", "description": "F1 comes from the model's own answer text.",
              "fix": "return the argument", "user_impact": "a caller gets the wrong number in normal use"}
REPLY_F1 = json.dumps({"reviewer": "opencode-glm-both", "verdict": "CHANGES_REQUESTED", "findings": [FINDING_F1]})
LIMIT_MSG = ("Rolling usage limit reached. It will reset in 3 hours 12 minutes. To continue using this model now, "
             "enable usage from your available balance - https://opencode.ai/workspace/wrk_demo/go")


def ev_start():
    return {"type": "step_start", "part": {"type": "step-start"}}


def ev_text(t):
    return {"type": "text", "part": {"type": "text", "text": t}}


def ev_reason(t):
    return {"type": "reasoning", "part": {"type": "reasoning", "text": t}}


def ev_tool(payload):
    return {"type": "tool_use", "part": {"type": "tool", "tool": "read", "state": {"status": "completed", "input": payload}}}


def ev_finish(reason="stop"):
    return {"type": "step_finish", "part": {"type": "step-finish", "reason": reason}}


def ev_error(msg, name="APIError"):
    return {"type": "error", "error": {"name": name, "data": {"message": msg}}}


def ids(rnd):
    return [x["id"] for x in (rnd.get("reviewers") or [])]


def row_of(rnd, rid):
    return next((x for x in (rnd.get("reviewers") or []) if x["id"] == rid), {})


def said(r):
    return (r.stdout or "") + (r.stderr or "")


def status_line(out, rid):
    for line in out.splitlines():
        if rid in line.split():
            return line
    return ""


def listed_available(out, rid):
    line = status_line(out, rid)
    return bool(line) and line.strip().startswith("✓")


def utcnow():
    return datetime.datetime.now(UTC)


def parse_time(s):
    return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


class Proj:
    """One throwaway project at the adversarial step of a risk-labelled step, with its own private PATH of fakes. The
    per-user folder is its own, or `home` (two lanes of one machine). `slots` is the project's review_depth for
    `risk`; `config` is extra `key=value` settings applied before the plan is approved."""

    def __init__(self, tag, fakes, providers, slots, risk="low", home=None, config=(), standards=False):
        b = Path(_tmp.mkdtemp(prefix="ratchet-fk-bin-"))
        for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
            p = shutil.which(n)
            if p:
                (b / n).symlink_to(p)
        for n in fakes:
            body = FAKE_OPENCODE.replace("__PY__", str(b / "python3")) if n == "opencode" else (
                FAKE_SH_ARG if n in ("cursor-agent", "copilot", "omp", "pi") else FAKE_SH)
            (b / n).write_text(body)
            (b / n).chmod(0o755)
        self.bin = b
        self.logs = Path(_tmp.mkdtemp(prefix="ratchet-fk-log-"))
        self.home = Path(home) if home else Path(_tmp.mkdtemp(prefix="ratchet-fk-user-"))
        self.rounds = 0
        g.ENV["RATCHET_HOME"] = str(self.home)
        try:
            self.dir = g.project(tag, extra_cp={"risk": risk},
                                 config=["providers=" + json.dumps(providers),
                                         "review_depth=" + json.dumps({risk: slots}), *config])
            if standards:    # written standards (named in the config) join every prompt; read-only after approval
                (self.dir / "STANDARDS.md").write_text(
                    "# Notes\n" + "".join(f"- (note {i}) keep functions small and name them for what they return.\n"
                                          for i in range(60)))
            g.approve_plan(self.dir)
            g.to_adversarial(self.dir)
            self.env = dict(g.ENV)
        finally:
            g.ENV["RATCHET_HOME"] = ORIG_HOME
        self.env["PATH"] = str(b)
        self.env["RATCHET_HOST_PROVIDER"] = "anthropic"
        self.env["RATCHET_FAKE_LOGS"] = str(self.logs)
        self.env["RATCHET_HOME"] = str(self.home)

    def run(self, *args):
        return g.rx(self.dir, *args, env=self.env)

    def script(self, tag, events=None, probe=None, exit_code=None, stderr=None, plain=None):
        """Script what the fake opencode model `tag` (glm, kimi, hi, off, def) answers; None leaves a file alone."""
        for name, val in (("script.json", events), ("probe.json", probe)):
            if val is not None:
                (self.bin / f"{tag}.{name}").write_text(json.dumps(val))
        for name, val in (("exit", exit_code), ("stderr", stderr), ("plain", plain)):
            if val is not None:
                (self.bin / f"{tag}.{name}").write_text(str(val))

    def unscript(self, tag):
        for f in self.bin.glob(f"{tag}.*"):
            f.unlink()

    def calls(self, kind=None, tag=None):
        p = self.logs / "calls.log"
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [c for c in rows if (kind is None or c["kind"] == kind) and (tag is None or c["tag"] == tag)]

    def sh_log(self, name):
        p = self.logs / f"{name}.log"
        return [x.split() for x in p.read_text().splitlines()] if p.exists() else []

    def open_round(self, nudge=False):
        if nudge:
            with open(self.dir / "src" / "f.js", "a") as f:
                f.write(f"// nudge {self.rounds}\n")
        self.rounds += 1
        return self.again()

    def again(self):
        r = self.run("reviewers", "CP1", "--json")
        try:
            rnd = json.loads(r.stdout) if r.returncode == 0 else {}
        except ValueError:
            rnd = {}
        return r, rnd

    def review(self, rnd, rid, extra=()):
        out = Path(rnd["output_dir"]) / f"{rid}.json"
        r = subprocess.run([sys.executable, str(EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out), *extra],
                           cwd=self.dir, capture_output=True, text=True, env=self.env, timeout=240)
        return r, out

    def dry_prompt(self, rnd, rid):
        """The exact prompt this reviewer would be sent now (external_review.py --dry-run keeps it), as bytes."""
        out = Path(rnd["output_dir"]) / f"{rid}.json"
        r = subprocess.run([sys.executable, str(EXT), "--reviewer", rid, "--cp", "CP1", "--out", str(out), "--dry-run"],
                           cwd=self.dir, capture_output=True, text=True, env=self.env, timeout=240)
        m = re.search(r"prompt written: (\S+)", r.stdout or "")
        if not m or not Path(m.group(1)).exists():
            return None
        data = Path(m.group(1)).read_bytes()
        shutil.rmtree(Path(m.group(1)).parent, ignore_errors=True)
        return data

    def status(self):
        return self.run("reviewer-status").stdout

    def health(self):
        p = self.home / "provider-health.json"
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except ValueError:
            return {}

    def starts(self, key):
        return len((self.health().get(key) or {}).get("starts") or [])

    def unavailable(self):
        p = self.dir / ".ratchet" / "unavailable-reviewers.json"
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except ValueError:
            return {}

    def markers(self, rnd):
        return sorted(x.name for x in Path(rnd["output_dir"]).glob("*.cutoff.json"))

    def marker(self, rnd, rid):
        p = Path(rnd["output_dir"]) / f"{rid}.cutoff.json"
        try:
            return json.loads(p.read_text()) if p.exists() else None
        except ValueError:
            return {}

    def reviews(self, rnd):
        return sorted(x.name for x in Path(rnd["output_dir"]).glob("*.json") if not x.name.endswith(".cutoff.json"))

    def big(self, lines=12000):
        """A new file of short lines under src/ (about 300,000 characters: the diff, capped at 180,000 characters,
        plus the instructions, is over 190,000 bytes), then `behavior` as the agent would run it."""
        (self.dir / "src" / "wide.js").write_text("".join(f"export const v{i:05d} = {i};\n" for i in range(lines)))
        return self.run("behavior", "CP1")

    def not_benched(self, key, mark_prefix):
        h = self.health().get(key) or {}
        un = self.unavailable()
        return ("bench" not in h and h.get("ok") is not False
                and not any(k.startswith(mark_prefix) for k in un))


def read_json(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}


def signed(review):
    sig = review.get("signature") or {}
    return sig.get("alg") == "hmac-sha256" and bool(re.fullmatch(r"[0-9a-f]{64}", str(sig.get("value", ""))))


def failed_attempt_checks(p, rnd, ext, out, key, rid, prefix, tag, requests=1, start_kept=True):
    """The set of outcomes every failed-attempt case checks: exit 3, no review file, no retry, a round-only cutoff
    marker, no bench anywhere, and the start kept."""
    check("external_review exits 3", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    check("no review file is written", not out.exists(), out)
    check(f"exactly {requests} review request reached the fake (no retry)", len(p.calls("review", tag)) == requests,
          p.calls("review", tag))
    check("a round-only cutoff marker with a reason exists in the round's folder",
          bool((p.marker(rnd, rid) or {}).get("reason")), p.markers(rnd))
    check("no bench or failed mark for the provider in provider-health.json or unavailable-reviewers.json",
          p.not_benched(key, prefix), (p.health().get(key), p.unavailable()))
    if start_kept:
        check("the review start the round recorded is kept", p.starts(key) == 1, p.health().get(key))


# ------------------------------------------------------------------------------------------------ cases

@case("CP7-T01")
def t01_a_review_of_about_200_kb_reaches_an_opencode_reviewer_whole():
    p = Proj("alpha", ["opencode"], [GLM], [["opencode-glm-both"]], config=["standards=[\"STANDARDS.md\"]"], standards=True)
    beh = p.big()
    need("set-up: behavior CP1 passes with the large file added", beh.returncode == 0, said(beh)[-300:])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    dry = p.dry_prompt(rnd, "opencode-glm-both")
    need("set-up: the dry run keeps the prompt, and it is over 190,000 bytes", dry is not None and len(dry) > 190_000,
         len(dry) if dry else None)
    ext, out = p.review(rnd, "opencode-glm-both")
    check("external_review exits 0", ext.returncode == 0, (ext.returncode, said(ext)[-400:]))
    review = read_json(out)
    check("the review file is written, signed, with verdict APPROVE and no findings",
          out.exists() and signed(review) and review.get("verdict") == "APPROVE" and review.get("findings") == [],
          review)
    calls = p.calls("review")
    check("the fake received exactly one review request", len(calls) == 1, calls)
    c = calls[-1] if calls else {}
    last = (p.logs / "last-stdin.txt")
    got = last.read_bytes() if last.exists() else b""
    check("the bytes the fake read from stdin equal the dry-run prompt's bytes, over 190,000", got == dry and len(got) > 190_000,
          (len(got), len(dry or b"")))
    check("the last stdin text ends with the prompt's closing instruction",
          got.decode("utf-8", "replace").rstrip().endswith(dry.decode("utf-8", "replace").rstrip()[-200:])
          and "An empty findings list means you approve" in got.decode("utf-8", "replace")[-1200:], got[-300:])
    check("the longest single argument the fake was given is under 1,000 bytes", 0 < c.get("longest", 10**9) < 1000, c)
    check("no argument is -f", "-f" not in c.get("argv", ["-f"]) and "--file" not in c.get("argv", ["-f"]), c.get("argv"))
    argv = c.get("argv", [])
    check("--format json is among its arguments", "--format" in argv and argv[argv.index("--format") + 1:][:1] == ["json"], argv)
    check("the shared record holds no bench for the provider and exactly one review start for it",
          p.not_benched(GLM_KEY, "opencode-glm") and p.starts(GLM_KEY) == 1, (p.health().get(GLM_KEY), p.unavailable()))


@case("CP7-T02")
def t02_a_finding_the_opencode_reviewer_writes_in_its_answer_text_is_recorded():
    p = Proj("bravo", ["opencode"], [GLM], [["opencode-glm-both"]])
    p.script("glm", events=[ev_start(), ev_text(REPLY_F1), ev_finish("stop")], exit_code=0)
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    ext, out = p.review(rnd, "opencode-glm-both")
    check("external_review exits 0", ext.returncode == 0, (ext.returncode, said(ext)[-400:]))
    review = read_json(out)
    fs = review.get("findings") or []
    check("the review records exactly finding F1 with its file and line",
          len(fs) == 1 and fs[0].get("id") == "F1" and fs[0].get("file") == "src/f.js" and fs[0].get("line") == 3, fs)
    check("the verdict is CHANGES_REQUESTED and the review is signed",
          review.get("verdict") == "CHANGES_REQUESTED" and signed(review), review)
    check("the finding text is the reply's own, not any raw event line",
          bool(fs) and fs[0].get("description") == FINDING_F1["description"]
          and not any(w in json.dumps(review) for w in ("step_start", "step_finish", "sessionID", "timestamp")),
          json.dumps(review)[:300])


@case("CP7-T03")
def t03_findings_shown_in_a_reasoning_event_or_echoed_input_never_count_as_the_review():
    example = json.dumps({"reviewer": "opencode-glm-both", "lens": "both", "checkpoint": "CP1", "fingerprint": "abc",
                          "verdict": "APPROVE or CHANGES_REQUESTED",
                          "findings": [{"id": "X1", "severity": "blocker", "kind": "reasoning-example", "file": "src/f.js",
                                        "line": 1, "rule": "r", "description": "REASONING-BLOCKER", "fix": "f",
                                        "user_impact": "u"}]})
    echoed = {"findings": [{"id": "E1", "severity": "major", "kind": "echoed-input", "file": "src/f.js", "line": 2,
                            "rule": "r", "description": "ECHOED-FINDING", "fix": "f", "user_impact": "u"}]}
    # first scenario: a clean text reply behind a reasoning blocker and an echoed finding
    p = Proj("charlie", ["opencode"], [GLM], [["opencode-glm-both"]])
    p.script("glm", events=[ev_start(), ev_reason(example), ev_tool(echoed), ev_text(CLEAN), ev_finish("stop")], exit_code=0)
    r, rnd = p.open_round()
    need("first scenario: the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    ext, out = p.review(rnd, "opencode-glm-both")
    review = read_json(out)
    check("first scenario: exit 0, zero findings and verdict APPROVE",
          ext.returncode == 0 and review.get("findings") == [] and review.get("verdict") == "APPROVE",
          (ext.returncode, said(ext)[-300:], review))
    body = out.read_text() if out.exists() else ""
    check("first scenario: neither the reasoning blocker nor the echoed finding appears anywhere in the review file",
          out.exists() and "REASONING-BLOCKER" not in body and "ECHOED-FINDING" not in body and '"X1"' not in body
          and '"E1"' not in body, body[:300])
    # second scenario: findings only in a reasoning event, prose only in the text event
    q = Proj("delta", ["opencode"], [GLM], [["opencode-glm-both"]])
    q.script("glm", events=[ev_start(), ev_reason(example), ev_text("I looked at the change and it seems fine to me."),
                            ev_finish("stop")], exit_code=0)
    r, rnd2 = q.open_round()
    need("second scenario: the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd2) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd2)))
    ext2, out2 = q.review(rnd2, "opencode-glm-both")
    check("second scenario: no findings JSON in any text event is a failed attempt: exit 3, no review file",
          ext2.returncode == 3 and not out2.exists(), (ext2.returncode, said(ext2)[-300:], out2.exists()))
    check("second scenario: no review was built from the reasoning event (the round's folder holds no review)",
          q.reviews(rnd2) == [], q.reviews(rnd2))


@case("CP7-T04")
def t04_an_empty_answer_with_exit_0_is_a_failed_attempt_for_this_round_only():
    p = Proj("echo", ["opencode"], [GLM, KIMI], [["opencode-glm-both"]])
    p.script("glm", events=[ev_start(), ev_reason(CLEAN), ev_finish("stop")], exit_code=0)
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    ext, out = p.review(rnd, "opencode-glm-both")
    check("the message says the model gave no answer text", "no answer text" in said(ext).lower(), said(ext)[-300:])
    failed_attempt_checks(p, rnd, ext, out, GLM_KEY, "opencode-glm-both", "opencode-glm", "glm")
    check("the round's folder holds no APPROVE review", p.reviews(rnd) == [], p.reviews(rnd))
    st = p.status()
    check("reviewer-status still lists opencode-glm-both as available", listed_available(st, "opencode-glm-both"),
          status_line(st, "opencode-glm-both") or st)


@case("CP7-T05")
def t05_after_an_empty_answer_the_slot_goes_to_a_different_working_provider():
    # first project: glm empty, kimi working; then the next round uses glm again
    p = Proj("foxtrot", ["opencode"], [GLM, KIMI], [["any-both"]])
    p.script("glm", events=[ev_start(), ev_reason(CLEAN), ev_finish("stop")], exit_code=0)
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"],
         (said(r)[-300:], ids(rnd)))
    ext, _out = p.review(rnd, "opencode-glm-both")
    need("set-up: the empty answer is a failed attempt (exit 3)", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    r2, rnd2 = p.again()
    check("the same round now has opencode-kimi-both in the slot, not a host reviewer",
          r2.returncode == 0 and rnd2.get("round") == rnd.get("round") and ids(rnd2) == ["opencode-kimi-both"],
          (said(r2)[-300:], ids(rnd2)))
    check("fallback_for lists opencode-glm-both",
          "opencode-glm-both" in (row_of(rnd2, "opencode-kimi-both").get("fallback_for") or []), row_of(rnd2, "opencode-kimi-both"))
    if "opencode-kimi-both" in ids(rnd2):
        k, kout = p.review(rnd2, "opencode-kimi-both")
        check("the kimi review succeeds", k.returncode == 0 and kout.exists(), (k.returncode, said(k)[-300:]))
    p.unscript("glm")                              # glm answers normally again
    with open(p.dir / "src" / "f.js", "a") as f:
        f.write("// changed after the empty answer\n")
    beh = p.run("behavior", "CP1")
    need("set-up: behavior CP1 passes on the edited code", beh.returncode == 0, said(beh)[-300:])
    r3, rnd3 = p.again()
    check("in the next round opencode-glm-both is picked again",
          r3.returncode == 0 and rnd3.get("round") == rnd.get("round", 1) + 1 and ids(rnd3) == ["opencode-glm-both"],
          (said(r3)[-300:], rnd3.get("round"), ids(rnd3)))
    if "opencode-glm-both" in ids(rnd3):
        g3, gout = p.review(rnd3, "opencode-glm-both")
        check("its normal answer is recorded as a pass (exit 0, APPROVE)",
              g3.returncode == 0 and read_json(gout).get("verdict") == "APPROVE", (g3.returncode, said(g3)[-300:]))
    # second project: kimi also answers empty, so the slot goes to the host's other-model reviewer, never back to glm
    q = Proj("golf", ["opencode"], [GLM, KIMI], [["any-both"]])
    q.script("glm", events=[ev_start(), ev_reason(CLEAN), ev_finish("stop")], exit_code=0)
    q.script("kimi", events=[ev_start(), ev_reason(CLEAN), ev_finish("stop")], exit_code=0)
    r, qr = q.open_round()
    need("second project: the round opens with opencode-glm-both", ids(qr) == ["opencode-glm-both"], (said(r)[-300:], ids(qr)))
    q.review(qr, "opencode-glm-both")
    r, qr2 = q.again()
    need("second project: the slot went to opencode-kimi-both", ids(qr2) == ["opencode-kimi-both"], ids(qr2))
    ek, _o = q.review(qr2, "opencode-kimi-both")
    need("second project: kimi's empty answer is a failed attempt", ek.returncode == 3, (ek.returncode, said(ek)[-300:]))
    r, qr3 = q.again()
    check("with kimi also empty the slot goes to host-alt-both and never to a repeat of glm in that round",
          r.returncode == 0 and ids(qr3) == ["host-alt-both"], (said(r)[-300:], ids(qr3)))


@case("CP7-T06")
def t06_a_reply_that_stopped_at_its_output_limit_is_a_failed_attempt_even_when_it_looks_complete():
    cut = '{"reviewer":"opencode-glm-both","verdict":"APPROVE","findings":[{"id":"X1","severity":"ma'
    a = Proj("hotel", ["opencode"], [GLM], [["opencode-glm-both"]])
    a.script("glm", events=[ev_start(), ev_text(cut), ev_finish("length")], exit_code=0)
    r, rnd = a.open_round()
    need("scenario A: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = a.review(rnd, "opencode-glm-both")
    failed_attempt_checks(a, rnd, ext, out, GLM_KEY, "opencode-glm-both", "opencode-glm", "glm")
    check("scenario A: the marker names the output limit", "output" in str((a.marker(rnd, "opencode-glm-both") or {}).get("reason")).lower(),
          a.marker(rnd, "opencode-glm-both"))
    b = Proj("india", ["opencode"], [GLM], [["opencode-glm-both"]])
    b.script("glm", events=[ev_start(), ev_text(CLEAN), ev_finish("length")], exit_code=0)
    r, rnd = b.open_round()
    need("scenario B: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = b.review(rnd, "opencode-glm-both")
    failed_attempt_checks(b, rnd, ext, out, GLM_KEY, "opencode-glm-both", "opencode-glm", "glm")
    check("scenario B: a complete reply with a 'length' finish never becomes a clean pass", b.reviews(rnd) == [], b.reviews(rnd))
    check("scenario B: the marker names the output limit", "output" in str((b.marker(rnd, "opencode-glm-both") or {}).get("reason")).lower(),
          b.marker(rnd, "opencode-glm-both"))
    c = Proj("juliet", ["opencode"], [GLM], [["opencode-glm-both"]])
    c.script("glm", events=[ev_start(), ev_text(CLEAN), ev_finish("stop")], exit_code=0)
    r, rnd = c.open_round()
    need("scenario C: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = c.review(rnd, "opencode-glm-both")
    check("scenario C (guard): the same complete reply with reason stop is recorded as APPROVE",
          ext.returncode == 0 and read_json(out).get("verdict") == "APPROVE", (ext.returncode, said(ext)[-300:]))


@case("CP7-T07")
def t07_an_error_event_that_names_a_usage_limit_still_benches_the_provider():
    shared = Path(_tmp.mkdtemp(prefix="ratchet-fk-user-"))
    lane1 = Proj("kilo", ["opencode"], [GLM], [["opencode-glm-both"]], home=shared)
    lane2 = Proj("lima", ["opencode"], [GLM], [["opencode-glm-both"]], home=shared)
    lane1.script("glm", events=[ev_start(), ev_error(LIMIT_MSG)], exit_code=1)
    r, rnd = lane1.open_round()
    need("scenario A: lane 1's round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    before = utcnow()
    ext, _out = lane1.review(rnd, "opencode-glm-both")
    after = utcnow()
    check("scenario A: external_review exits 3", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    bench = (lane1.health().get(GLM_KEY) or {}).get("bench") or {}
    until = parse_time(bench["until"]) if bench.get("until") else None
    want = datetime.timedelta(hours=3, minutes=12)
    slack = datetime.timedelta(seconds=60)
    check("scenario A: provider-health.json benches the provider until about 3 hours 12 minutes ahead (within a minute)",
          until is not None and before + want - slack <= until <= after + want + slack, bench)
    r2, rnd2 = lane2.open_round()
    check("scenario A: lane 2's round does not offer opencode-glm-both", r2.returncode == 0 and "opencode-glm-both" not in ids(rnd2),
          (said(r2)[-300:], ids(rnd2)))
    # scenario B: the same words only in a reasoning event, a normal clean answer in the text event
    other = Proj("mike", ["opencode"], [GLM], [["opencode-glm-both"]])
    other.script("glm", events=[ev_start(), ev_reason("The earlier run said: " + LIMIT_MSG), ev_text(CLEAN),
                                ev_finish("stop")], exit_code=0)
    r, rnd3 = other.open_round()
    need("scenario B: the round opens with opencode-glm-both", ids(rnd3) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd3)))
    ext3, out3 = other.review(rnd3, "opencode-glm-both")
    check("scenario B: exit 0 and the review is recorded as APPROVE",
          ext3.returncode == 0 and read_json(out3).get("verdict") == "APPROVE", (ext3.returncode, said(ext3)[-300:]))
    check("scenario B: no bench is written", "bench" not in (other.health().get(GLM_KEY) or {}), other.health().get(GLM_KEY))


def size_failure_checks(p, rnd, ext, out, pid, prog, key):
    rid = f"{pid}-both"
    check(f"{pid}: external_review exits 3 with a plain 'too large for a command-line argument' reason, no traceback",
          ext.returncode == 3 and "too large" in said(ext) and "command-line argument" in said(ext)
          and "Traceback" not in said(ext), (ext.returncode, said(ext)[-400:]))
    check(f"{pid}: no review file is written", not out.exists(), out)
    check(f"{pid}: the fake program was never started", p.sh_log(prog) == [], p.sh_log(prog))
    h = p.health().get(key) or {}
    check(f"{pid}: no bench, no failed mark and no failed test result for it anywhere",
          "bench" not in h and h.get("ok") is not False and not any(k.startswith(pid) for k in p.unavailable()),
          (h, p.unavailable()))
    check(f"{pid}: the review start is given back (zero starts for it)", p.starts(key) == 0, h)
    check(f"{pid}: the round's folder holds a cutoff marker for that reviewer only", p.markers(rnd) == [f"{rid}.cutoff.json"],
          p.markers(rnd))
    st = p.status()
    check(f"{pid}: reviewer-status lists the provider's reviewers as available",
          all(listed_available(st, f"{pid}-{lens}") for lens in ("both", "standards", "correctness")), st)


@case("CP7-T08")
def t08_a_prompt_too_large_for_a_cli_that_takes_it_as_an_argument_never_benches_that_provider():
    for pid, (prog, pv) in ARG_PROVIDERS.items():
        p = Proj("nov" + pid[:2], [prog, "codex"], [pv, CODEX], [[f"{pid}-both"]], config=["probe_providers=false"])
        beh = p.big()
        need(f"{pid}: set-up: behavior CP1 passes with the large file added", beh.returncode == 0, said(beh)[-300:])
        r, rnd = p.open_round()
        need(f"{pid}: the round opens with {pid}-both", r.returncode == 0 and ids(rnd) == [f"{pid}-both"],
             (said(r)[-300:], ids(rnd)))
        ext, out = p.review(rnd, f"{pid}-both")
        size_failure_checks(p, rnd, ext, out, pid, prog, pid)
        r2, rnd2 = p.again()
        check(f"{pid}: reviewers CP1 in the same round moves the slot to the other working provider, never to a repeat",
              r2.returncode == 0 and ids(rnd2) == ["codex-both"], (said(r2)[-300:], ids(rnd2)))


@case("CP7-T09")
def t09_another_lane_and_the_providers_next_review_are_unaffected_by_a_size_failure():
    pid, (prog, pv) = "omp", ARG_PROVIDERS["omp"]
    shared = Path(_tmp.mkdtemp(prefix="ratchet-fk-user-"))
    lane1 = Proj("oscar", [prog, "codex"], [pv, CODEX], [[f"{pid}-both"]], home=shared, config=["probe_providers=false"])
    lane2 = Proj("papa", [prog, "codex"], [pv, CODEX], [[f"{pid}-both"]], home=shared, config=["probe_providers=false"])
    beh = lane1.big()
    need("set-up: behavior CP1 passes with the large file added", beh.returncode == 0, said(beh)[-300:])
    r, rnd = lane1.open_round()
    need("set-up: lane 1's round opens with omp-both", ids(rnd) == ["omp-both"], (said(r)[-300:], ids(rnd)))
    ext, _out = lane1.review(rnd, "omp-both")
    need("set-up: the large prompt fails for lane 1 (exit 3)", ext.returncode == 3, (ext.returncode, said(ext)[-300:]))
    t0 = time.monotonic()
    r2, rnd2 = lane2.open_round()
    check("lane 2 is offered omp-both (not skipped for benching)", r2.returncode == 0 and ids(rnd2) == ["omp-both"],
          (said(r2)[-300:], ids(rnd2)))
    if "omp-both" in ids(rnd2):
        e2, o2 = lane2.review(rnd2, "omp-both")
        got = lane2.sh_log(prog)
        check("lane 2's omp receives the small prompt and returns a clean answer",
              e2.returncode == 0 and read_json(o2).get("verdict") == "APPROVE"
              and len(got) == 1 and got[0][0] == "review" and 0 < int(got[0][1]) < 131072, (e2.returncode, said(e2)[-300:], got))
    (lane1.dir / "src" / "wide.js").unlink()
    with open(lane1.dir / "src" / "f.js", "a") as f:
        f.write("// large file removed\n")
    beh = lane1.run("behavior", "CP1")
    need("set-up: behavior CP1 passes on lane 1's smaller code", beh.returncode == 0, said(beh)[-300:])
    r3, rnd3 = lane1.again()
    check("lane 1's next round also picks omp-both", r3.returncode == 0 and ids(rnd3) == ["omp-both"],
          (said(r3)[-300:], ids(rnd3)))
    if "omp-both" in ids(rnd3):
        e3, o3 = lane1.review(rnd3, "omp-both")
        check("lane 1's next review is recorded", e3.returncode == 0 and read_json(o3).get("verdict") == "APPROVE",
              (e3.returncode, said(e3)[-300:]))
    check("neither run waits for any retest time", time.monotonic() - t0 < 120, time.monotonic() - t0)
    check("no bench for omp is written by either lane", "bench" not in (lane1.health().get(pid) or {}), lane1.health().get(pid))


def variant_after(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--variant" and i + 1 < len(argv)]


@case("CP7-T10")
def t10_opencode_review_and_test_requests_ask_for_low_thinking_by_default():
    p = Proj("quebec", ["opencode"], [], [["opencode-glm-both"]])
    add = p.run("providers", "--add", "opencode-glm", "--preset", "opencode", "--model", "opencode-go/glm-5.3", "--no-check")
    need("set-up: providers --add opencode-glm (no variant) succeeds", add.returncode == 0, said(add)[-300:])
    chk = p.run("providers", "--check", "opencode-glm", "--json")
    need("set-up: providers --check opencode-glm --json succeeds", chk.returncode == 0, said(chk)[-300:])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, _out = p.review(rnd, "opencode-glm-both")
    need("set-up: the review runs", ext.returncode == 0, (ext.returncode, said(ext)[-300:]))
    for kind in ("probe", "review"):
        calls = p.calls(kind)
        need(f"the fake logged a {kind} request", bool(calls), p.calls())
        c = calls[-1]
        argv = c["argv"]
        check(f"{kind}: --variant is followed by low exactly once", variant_after(argv) == ["low"] and argv.count("--variant") == 1, argv)
        check(f"{kind}: --format json is present", "--format" in argv and argv[argv.index("--format") + 1] == "json", argv)
        check(f"{kind}: -m opencode-go/glm-5.3 is present", "-m" in argv and argv[argv.index("-m") + 1] == "opencode-go/glm-5.3", argv)
        check(f"{kind}: the message argument is short and the request text is on stdin only",
              c["msg_len"] < 500 and not c["msg_has_check"] and c["longest"] < 1000 and c["stdin_len"] > 0, c)


@case("CP7-T11")
def t11_a_provider_can_set_its_own_thinking_level_or_turn_the_cap_off():
    models = {"hi": "opencode-go/qwen3-hi", "off": "opencode-go/qwen3-off", "def": "opencode-go/qwen3-def"}
    p = Proj("romeo", ["opencode"], [], [["opencode-hi-both"], ["opencode-off-both"], ["opencode-def-both"]], risk="normal")
    adds = {"hi": ["--variant", "high"], "off": ["--variant", "none"], "def": []}
    for t, extra in adds.items():
        a = p.run("providers", "--add", f"opencode-{t}", "--preset", "opencode", "--model", models[t], "--no-check", *extra)
        need(f"set-up: providers --add opencode-{t} {' '.join(extra)} succeeds", a.returncode == 0, said(a)[-300:])
    for t in adds:
        c = p.run("providers", "--check", f"opencode-{t}", "--json")
        need(f"set-up: providers --check opencode-{t} succeeds", c.returncode == 0, said(c)[-300:])
    r, rnd = p.open_round()
    need("the round opens with the three reviewers", r.returncode == 0 and sorted(ids(rnd)) == ["opencode-def-both", "opencode-hi-both", "opencode-off-both"],
         (said(r)[-300:], ids(rnd)))
    for t in adds:
        e, _o = p.review(rnd, f"opencode-{t}-both")
        need(f"set-up: the opencode-{t} review runs", e.returncode == 0, (e.returncode, said(e)[-300:]))
    for kind in ("probe", "review"):
        hi, off, df = p.calls(kind, "hi"), p.calls(kind, "off"), p.calls(kind, "def")
        need(f"the fake logged a {kind} request for each of the three", bool(hi and off and df), p.calls())
        check(f"{kind}: opencode-hi is sent --variant high", variant_after(hi[-1]["argv"]) == ["high"], hi[-1]["argv"])
        check(f"{kind}: opencode-off is sent no --variant argument at all", "--variant" not in off[-1]["argv"], off[-1]["argv"])
        check(f"{kind}: opencode-def is sent --variant low", variant_after(df[-1]["argv"]) == ["low"], df[-1]["argv"])
    lst = p.run("providers", "--json")
    try:
        rows = {x["id"] for x in json.loads(lst.stdout).get("providers", [])}
    except ValueError:
        rows = set()
    check("providers --json still lists all three", {"opencode-hi", "opencode-off", "opencode-def"} <= rows, (rows, said(lst)[-200:]))
    again = p.run("providers", "--add", "opencode-hi", "--preset", "opencode", "--model", models["hi"], "--no-check")
    need("re-adding opencode-hi without --variant succeeds", again.returncode == 0, said(again)[-300:])
    n = len(p.calls("probe", "hi"))
    p.run("providers", "--check", "opencode-hi", "--json")
    hi = p.calls("probe", "hi")
    check("re-adding opencode-hi without --variant keeps high", len(hi) == n + 1 and variant_after(hi[-1]["argv"]) == ["high"],
          [c["argv"] for c in hi])


@case("CP7-T12")
def t12_the_provider_test_request_still_passes_and_an_echoed_request_alone_is_not_a_pass():
    def row_of_check(p):
        c = p.run("providers", "--check", "opencode-glm", "--json")
        try:
            rows = json.loads(c.stdout).get("providers", [])
        except ValueError:
            rows = []
        return c, next((x for x in rows if x.get("id") == "opencode-glm"), {})
    a = Proj("sierra", ["opencode"], [GLM], [["opencode-glm-both"]], config=["probe_providers=false"])
    c, row = row_of_check(a)
    check("scenario A: the row is ok with state working", c.returncode == 0 and row.get("ok") is True
          and str(row.get("state", "")).startswith("working"), (said(c)[-300:], row))
    check("scenario A: exactly one request reached the fake", len(a.calls("probe")) == 1, a.calls())
    b = Proj("tango", ["opencode"], [GLM], [["opencode-glm-both"]], config=["probe_providers=false"])
    b.script("glm", probe=[ev_start(), ev_tool({"prompt": "{{REQUEST}}"}), ev_reason("{{REQUEST}}"),
                           ev_text("I am sorry, I cannot comply with that request."), ev_finish("stop")], exit_code=0)
    c, row = row_of_check(b)
    check("scenario B: the request was really sent and echoed (set-up)", len(b.calls("probe")) == 1, b.calls())
    check("scenario B: the row is not ok because the token is not in the answer text",
          c.returncode == 0 and row.get("ok") is False and not str(row.get("state", "")).startswith("working"), (said(c)[-300:], row))


@case("CP7-T13")
def t13_a_cli_that_ignores_format_json_and_prints_plain_text_is_read_as_before():
    minor = {"id": "M1", "severity": "minor", "kind": "naming", "file": "src/f.js", "line": 1, "rule": "naming rule",
             "description": "PLAIN-MINOR-FINDING", "fix": "rename it"}
    p = Proj("uniform", ["opencode"], [GLM], [["opencode-glm-both"]])
    p.script("glm", plain=json.dumps({"reviewer": "opencode-glm-both", "verdict": "CHANGES_REQUESTED", "findings": [minor]}),
             exit_code=0)
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", r.returncode == 0 and ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = p.review(rnd, "opencode-glm-both")
    review = read_json(out)
    fs = review.get("findings") or []
    check("the plain-text finding is recorded and the verdict is CHANGES_REQUESTED",
          ext.returncode == 0 and len(fs) == 1 and fs[0].get("id") == "M1" and fs[0].get("description") == "PLAIN-MINOR-FINDING"
          and review.get("verdict") == "CHANGES_REQUESTED", (ext.returncode, said(ext)[-300:], review))
    p.script("glm", plain=CLEAN, exit_code=0)
    ext2, out2 = p.review(rnd, "opencode-glm-both")
    review2 = read_json(out2)
    check("a plain-text reply with an empty findings list is recorded as APPROVE",
          ext2.returncode == 0 and review2.get("findings") == [] and review2.get("verdict") == "APPROVE",
          (ext2.returncode, said(ext2)[-300:], review2))


@case("CP7-T14")
def t14_the_docs_and_the_test_list_describe_the_new_opencode_behavior():
    docs = {"README.md": ROOT / "README.md",
            "orchestrator SKILL.md": ROOT / "skills" / "ratchet-orchestrator" / "SKILL.md",
            "adversarial-loop SKILL.md": ROOT / "skills" / "ratchet-adversarial-loop" / "SKILL.md",
            "schemas.md": ROOT / "skills" / "ratchet-orchestrator" / "references" / "schemas.md"}
    text = {k: v.read_text() for k, v in docs.items()}
    check("README's test list contains python3 tests/test_opencode_big.py", "python3 tests/test_opencode_big.py" in text["README.md"], "not in README")
    for name, t in text.items():
        low = t.lower()
        check(f"{name}: says OpenCode gets the review prompt on stdin, not as one argument",
              bool(re.search(r"opencode[^.]{0,200}stdin|stdin[^.]{0,200}opencode", low)), "not found")
        check(f"{name}: says an answer with no text or a 'length' finish is a failed attempt for that round, never an approval",
              "length" in low and bool(re.search(r"no (?:answer )?text|empty answer|no answer", low)), "not found")
        check(f"{name}: says a prompt too large for a command line moves only that slot and never benches a provider",
              bool(re.search(r"too large[^.]{0,250}(?:command line|command-line)", low))
              and bool(re.search(r"never[^.]{0,80}bench|not[^.]{0,60}bench", low)), "not found")
        check(f"{name}: says --variant low is the default thinking cap, settable per provider, 'none' turns it off",
              "--variant low" in low and "none" in low and "variant" in low, "not found")
    check("schemas.md lists the provider field variant", bool(re.search(r"`variant`|\"variant\"", text["schemas.md"])), "not found")
    src = (ROOT / "skills" / "ratchet-adversarial-loop" / "scripts" / "external_review.py").read_text()
    doc = src.split('"""')[1] if src.count('"""') >= 2 else ""
    at = doc.find("only as a command-line argument")
    check("the external_review.py docstring still has the argument-delivery paragraph", at >= 0, doc[:100])
    check("the external_review.py docstring no longer names opencode among the CLIs that take the prompt only as an argument",
          at >= 0 and "opencode" not in doc[at:at + 260].lower(), doc[at:at + 260])


# ------------------------------------------------------------------------------------------------ runner

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
    global _CUR_CASE, FAIL
    for cid, fn in TESTS:
        _CUR_CASE = cid
        CASE_STATUS.setdefault(cid, True)
        print(f"{cid}: {fn.__name__}")
        try:
            fn()
        except Stop:
            pass                       # reported as a failed check already
        except Exception as e:         # a crash is this case's failed result, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {e}")
            print(f"  ✗ [{cid}] CRASHED: {e}")
            traceback.print_exc()
        finally:
            g.ENV["RATCHET_HOME"] = ORIG_HOME
    _CUR_CASE = None
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


# ------------------------------------------------------------------------------------------------ fix round 1

@case("CP7-X01")
def x01_every_opencode_request_carries_one_fixed_session_title():
    p = Proj("papa", ["opencode"], [GLM], [["opencode-glm-both"]], config=["probe_providers=false"])
    r, rnd = p.open_round()
    need("the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = p.review(rnd, "opencode-glm-both")
    need("set-up: the review succeeds", ext.returncode == 0 and out.exists(), (ext.returncode, said(ext)[-300:]))
    chk = p.run("providers", "--check", "opencode-glm", "--json")
    need("set-up: the test request is sent", chk.returncode == 0 and len(p.calls("probe")) == 1, said(chk)[-300:])
    reviews, probes = p.calls("review"), p.calls("probe")
    check("every review request carries --title=ratchet review exactly once (so OpenCode names no session from the prompt)",
          len(reviews) == 1 and all(c["argv"].count("--title=ratchet review") == 1
                                    and sum(a.startswith("--title") for a in c["argv"]) == 1 for c in reviews), reviews)
    check("the test request carries only its own title, --title=connectivity check, once",
          all(c["argv"].count("--title=connectivity check") == 1 and sum(a.startswith("--title") for a in c["argv"]) == 1
              for c in probes), probes)


@case("CP7-X02")
def x02_a_length_finish_or_empty_answer_fails_the_attempt_even_with_an_error_event():
    plain = ev_error("Compaction failed: provider returned an unexpected response", name="UnknownError")
    a = Proj("quebec", ["opencode"], [GLM], [["opencode-glm-both"]])
    a.script("glm", events=[ev_start(), ev_text(CLEAN), ev_finish("length"), plain], exit_code=1)
    r, rnd = a.open_round()
    need("scenario A: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = a.review(rnd, "opencode-glm-both")
    failed_attempt_checks(a, rnd, ext, out, GLM_KEY, "opencode-glm-both", "opencode-glm", "glm")
    check("scenario A: a complete reply with a length finish and an error event is never recorded", a.reviews(rnd) == [], a.reviews(rnd))
    b = Proj("romeo", ["opencode"], [GLM], [["opencode-glm-both"]])
    b.script("glm", events=[ev_start(), ev_finish("stop"), plain], exit_code=0)
    r, rnd = b.open_round()
    need("scenario B: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = b.review(rnd, "opencode-glm-both")
    check("scenario B: exit 0 with a non-quota error event and an empty answer is a provider failure (exit 3, marked unavailable)",
          ext.returncode == 3 and not out.exists() and b.markers(rnd) == [] and not b.not_benched(GLM_KEY, "opencode-glm"),
          (ext.returncode, said(ext)[-300:], b.markers(rnd), b.unavailable()))
    c = Proj("sierra2", ["opencode"], [GLM], [["opencode-glm-both"]])
    c.script("glm", events=[ev_start(), ev_text(CLEAN), ev_finish("length"), ev_error(LIMIT_MSG)], exit_code=1)
    r, rnd = c.open_round()
    need("scenario C: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = c.review(rnd, "opencode-glm-both")
    bench = (c.health().get(GLM_KEY) or {}).get("bench") or {}
    check("scenario C: an error event that names a usage limit still benches the provider and records no review",
          ext.returncode == 3 and bool(bench.get("quota")) and not out.exists() and c.reviews(rnd) == [],
          (ext.returncode, said(ext)[-300:], bench))


@case("CP7-X03")
def x03_a_prose_reply_from_a_clean_run_is_a_failed_attempt_for_this_round_only():
    prose = "I looked at the change and it seems fine to me."
    a = Proj("tango2", ["opencode"], [GLM, KIMI], [["any-both"]])
    a.script("glm", events=[ev_start(), ev_text(prose), ev_finish("stop")], exit_code=0)
    r, rnd = a.open_round()
    need("scenario A: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = a.review(rnd, "opencode-glm-both")
    failed_attempt_checks(a, rnd, ext, out, GLM_KEY, "opencode-glm-both", "opencode-glm", "glm")
    r2, rnd2 = a.again()
    check("scenario A: the slot goes to opencode-kimi-both in the same round",
          r2.returncode == 0 and ids(rnd2) == ["opencode-kimi-both"], (said(r2)[-300:], ids(rnd2)))
    b = Proj("uniform", ["opencode"], [GLM], [["opencode-glm-both"]])
    b.script("glm", events=[ev_start(), ev_text(prose), ev_finish("stop")], exit_code=1)
    r, rnd = b.open_round()
    need("scenario B: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = b.review(rnd, "opencode-glm-both")
    check("scenario B (guard): the same prose from a run that exited 1 is still a provider failure (exit 3, marked unavailable)",
          ext.returncode == 3 and not out.exists() and b.markers(rnd) == [] and not b.not_benched(GLM_KEY, "opencode-glm"),
          (ext.returncode, said(ext)[-300:], b.markers(rnd), b.unavailable()))


# ------------------------------------------------------------------------------------------------ fix round 2

@case("CP7-X04")
def x04_exit_0_runs_follow_one_rule_quota_is_read_from_errors_and_stderr_never_the_answer():
    quoted = "The new banner shows \"Usage limit reached, try again in 24 hours\" and looks fine to me."
    plain = ev_error("Model not found: opencode-go/glm-9", name="UnknownError")
    a = Proj("victor", ["opencode"], [GLM], [["opencode-glm-both"]])
    a.script("glm", events=[ev_start(), ev_text(quoted), ev_finish("stop")], exit_code=0)
    r, rnd = a.open_round()
    need("scenario A: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = a.review(rnd, "opencode-glm-both")
    failed_attempt_checks(a, rnd, ext, out, GLM_KEY, "opencode-glm-both", "opencode-glm", "glm")
    check("scenario A: a prose answer that quotes a usage limit message writes no bench", "bench" not in (a.health().get(GLM_KEY) or {}),
          a.health().get(GLM_KEY))
    for tag, events in (("whiskey", [ev_start(), ev_finish("stop"), plain]),
                        ("xray", [ev_start(), ev_text(quoted), ev_finish("stop"), plain])):
        b = Proj(tag, ["opencode"], [GLM], [["opencode-glm-both"]])
        b.script("glm", events=events, exit_code=0)
        r, rnd = b.open_round()
        need(f"{tag}: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
        ext, out = b.review(rnd, "opencode-glm-both")
        check(f"{tag}: exit 0 with a non-quota error event is a provider failure: exit 3, marked unavailable, no cutoff marker",
              ext.returncode == 3 and not out.exists() and b.markers(rnd) == [] and not b.not_benched(GLM_KEY, "opencode-glm"),
              (ext.returncode, said(ext)[-300:], b.markers(rnd), b.unavailable()))
        check(f"{tag}: it is not a quota bench", "bench" not in (b.health().get(GLM_KEY) or {}) or not b.health()[GLM_KEY]["bench"].get("quota"),
              b.health().get(GLM_KEY))
    c = Proj("yankee", ["opencode"], [GLM], [["opencode-glm-both"]])
    c.script("glm", events=[ev_start(), ev_finish("stop")], exit_code=0, stderr=LIMIT_MSG + "\n")
    r, rnd = c.open_round()
    need("scenario C: the round opens with opencode-glm-both", ids(rnd) == ["opencode-glm-both"], (said(r)[-300:], ids(rnd)))
    ext, out = c.review(rnd, "opencode-glm-both")
    bench = (c.health().get(GLM_KEY) or {}).get("bench") or {}
    check("scenario C: a limit message only on stderr with an empty answer gets the quota bench",
          ext.returncode == 3 and not out.exists() and bool(bench.get("quota")), (ext.returncode, said(ext)[-300:], bench))


if __name__ == "__main__":
    main()
