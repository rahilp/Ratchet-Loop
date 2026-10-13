#!/usr/bin/env python3
"""
CP1 tests: checking a reviewer name tests its provider.   python3 tests/test_quota_check.py

`ratchet.py providers --check <name>` takes a provider id, `all`, or a reviewer name made of a provider id
and a lens (`codex-correctness`, `opencode-glm-standards`). A reviewer name sends one test request to
that reviewer's provider only; a name that matches nothing fails with the known provider ids and sends
nothing.

Every case builds a throwaway project (tests/test_gates.py's `project()`) with the host set to anthropic, its
own per-user folder, and a private PATH holding only fake `codex`, `gemini` and `opencode` scripts (plus the
few real binaries the engine itself needs). Each fake appends one line per call (its name and arguments) to its
own log file under $RATCHET_FAKE_LOGS and answers the connectivity check with RATCHET-PROBE-OK, or fails with
"error: rate limited" and exit 1 when a `<name>.fail` marker sits beside it. "Sent a test request" is judged from
those logs and from the rows `providers ... --json` prints. No real provider CLI is ever called and no money is
spent. Temp project names are neutral, never the words in a case's own key.

Each case is its own function in TESTS, run from main() inside try/except, so a crash is a failed case, never a
silent drop. Writes a per-test JSON report to RATCHET_TEST_REPORT when set:
{"cases": [{"id": "CP1-T01", "status": "passed"|"failed", "message": "..."}]}.
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
import test_gates as g          # noqa: E402  (project, rx, ENV)

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

# One fake agent CLI for every name. It logs "<name> <args>" to $RATCHET_FAKE_LOGS/<name>.log before doing
# anything else, fails with a fixed reason when <name>.fail sits beside it, and otherwise answers the
# connectivity check (prompt on stdin or in the arguments) with RATCHET-PROBE-OK.
FAKE = r"""#!/bin/sh
me=$(basename "$0")
here=$(dirname "$0")
[ -n "$RATCHET_FAKE_LOGS" ] && printf '%s %s\n' "$me" "$*" >> "$RATCHET_FAKE_LOGS/$me.log"
if [ -f "$here/$me.fail" ]; then echo "error: rate limited" >&2; exit 1; fi
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

CODEX_GEMINI = [{"id": "codex", "preset": "codex"}, {"id": "gemini", "preset": "gemini"}]
OPENCODE_TWO = [
    {"id": "opencode-glm", "preset": "opencode", "model": "opencode-go/glm-5.3", "cost": "flat"},
    {"id": "opencode-kimi", "preset": "opencode", "model": "opencode-go/kimi-k3", "cost": "flat"},
]


class Case:
    """A throwaway project, its private PATH of fakes and its own per-user folder."""

    def __init__(self, name, provider_list, fakes):
        b = Path(_tmp.mkdtemp(prefix="ratchet-fk-bin-"))
        for n in ("python3", "git", "node", "sh", "env", "cat", "dirname", "basename"):
            p = shutil.which(n)
            if p:
                (b / n).symlink_to(p)
        for n in fakes:
            (b / n).write_text(FAKE)
            (b / n).chmod(0o755)
        self.bin = b
        self.logs = Path(_tmp.mkdtemp(prefix="ratchet-fk-log-"))
        self.env = dict(g.ENV)
        self.env["PATH"] = str(b)
        self.env["RATCHET_HOST_PROVIDER"] = "anthropic"
        self.env["RATCHET_FAKE_LOGS"] = str(self.logs)
        self.dir = g.project(name)
        # own per-user folder for the providers commands, so no case sees another's saved results
        self.env["RATCHET_HOME"] =str(Path(_tmp.mkdtemp(prefix="ratchet-fk-user-")))
        r = g.rx(self.dir, "config", "--set", "providers=" + json.dumps(provider_list), env=self.env)
        assert r.returncode == 0, r.stdout + r.stderr

    def fail(self, name, on=True):
        marker = self.bin / f"{name}.fail"
        if on:
            marker.write_text("x")
        elif marker.exists():
            marker.unlink()

    def calls(self, name):
        """The lines the fake `name` logged: one per call it received."""
        p = self.logs / f"{name}.log"
        return p.read_text().splitlines() if p.exists() else []

    def run(self, *args):
        return g.rx(self.dir, "providers", *args, env=self.env)

    def rows(self, *args):
        """(process, {provider id: row}) for `providers <args> --json`."""
        r = self.run(*args, "--json")
        try:
            out = json.loads(r.stdout)
            rows = {x["id"]: x for x in out.get("providers") or []}
        except (ValueError, TypeError, KeyError):
            rows = {}
        return r, rows

    def saved_rows(self):
        """The provider rows as saved results show them, with no check and no request."""
        return self.rows()[1]


def said(r) -> str:
    return (r.stdout or "") + (r.stderr or "")


# ------------------------------------------------------------------------------------------------ cases

def t01_reviewer_name_tests_its_provider_and_shows_fresh_result():
    c = Case("chk-a", CODEX_GEMINI, ["codex", "gemini"])
    c.fail("codex")
    c.run("--check", "codex")                      # leaves an old failing result behind
    before = c.saved_rows().get("codex") or {}
    check("[CP1-T01] set-up: the old saved codex result says failing", before.get("ok") is False
          and "failing" in str(before.get("state")), before)
    c.fail("codex", on=False)
    codex_before, gemini_before = len(c.calls("codex")), len(c.calls("gemini"))
    r, rows = c.rows("--check", "codex-correctness")
    check("[CP1-T01] providers --check codex-correctness --json succeeds", r.returncode == 0, said(r))
    check("[CP1-T01] the codex fake gains exactly one test request", len(c.calls("codex")) == codex_before + 1,
          c.calls("codex"))
    check("[CP1-T01] the gemini fake gains none", len(c.calls("gemini")) == gemini_before, c.calls("gemini"))
    row = rows.get("codex") or {}
    check("[CP1-T01] the codex row in the output is ok (working), not the old failing result",
          row.get("ok") is True and str(row.get("state", "")).startswith("working"), row)
    saved = c.saved_rows().get("codex") or {}
    check("[CP1-T01] the saved provider result for codex is now ok", saved.get("ok") is True
          and str(saved.get("state", "")).startswith("working"), saved)


def t02_provider_id_check_unchanged():
    c = Case("chk-b", CODEX_GEMINI, ["codex", "gemini"])
    r, rows = c.rows("--check", "codex")
    check("[CP1-T02] providers --check codex --json succeeds", r.returncode == 0, said(r))
    check("[CP1-T02] exactly one test request reaches the codex fake", len(c.calls("codex")) == 1, c.calls("codex"))
    check("[CP1-T02] none reaches the gemini fake", c.calls("gemini") == [], c.calls("gemini"))
    check("[CP1-T02] the codex row is ok", (rows.get("codex") or {}).get("ok") is True, rows.get("codex"))


def t03_all_still_tests_every_installed_provider():
    c = Case("chk-c", CODEX_GEMINI, ["codex", "gemini"])
    r, rows = c.rows("--check", "all")
    check("[CP1-T03] providers --check all --json succeeds", r.returncode == 0, said(r))
    check("[CP1-T03] one test request reaches the codex fake", len(c.calls("codex")) == 1, c.calls("codex"))
    check("[CP1-T03] one test request reaches the gemini fake", len(c.calls("gemini")) == 1, c.calls("gemini"))
    check("[CP1-T03] the codex row is ok", (rows.get("codex") or {}).get("ok") is True, rows.get("codex"))
    check("[CP1-T03] the gemini row is ok", (rows.get("gemini") or {}).get("ok") is True, rows.get("gemini"))


def t04_unknown_name_fails_and_tests_nothing():
    c = Case("chk-d", CODEX_GEMINI, ["codex", "gemini"])
    for name in ("mystery-correctness", "mystery"):
        r = c.run("--check", name)
        msg = said(r)
        check(f"[CP1-T04] providers --check {name} exits non-zero", r.returncode != 0, msg)
        check(f"[CP1-T04] the message for {name} names the unknown name", name in msg, msg)
        check(f"[CP1-T04] the message for {name} lists the known provider ids (codex and gemini)",
              re.search(r"\bcodex\b", msg) is not None and re.search(r"\bgemini\b", msg) is not None, msg)
        check(f"[CP1-T04] the message for {name} is a plain message, not a traceback", "Traceback" not in msg, msg)
    check("[CP1-T04] neither fake has any call", c.calls("codex") == [] and c.calls("gemini") == [],
          (c.calls("codex"), c.calls("gemini")))
    rows = c.saved_rows()
    check("[CP1-T04] no provider result is saved for either provider",
          all(not (rows.get(i) or {}).get("ok") and "not tested yet" in str((rows.get(i) or {}).get("state"))
              for i in ("codex", "gemini")), rows)


def t05_shared_program_tests_only_that_model():
    c = Case("chk-e", OPENCODE_TWO, ["opencode"])
    r, rows = c.rows("--check", "opencode-glm-standards")
    check("[CP1-T05] providers --check opencode-glm-standards --json succeeds", r.returncode == 0, said(r))
    calls = c.calls("opencode")
    check("[CP1-T05] the fake's log has exactly one test request", len(calls) == 1, calls)
    check("[CP1-T05] that request names opencode-go/glm-5.3", len(calls) == 1 and "opencode-go/glm-5.3" in calls[0],
          calls)
    check("[CP1-T05] no call names kimi", not any("kimi" in x for x in calls), calls)
    check("[CP1-T05] the opencode-glm row is ok", (rows.get("opencode-glm") or {}).get("ok") is True,
          rows.get("opencode-glm"))
    kimi = rows.get("opencode-kimi") or {}
    check("[CP1-T05] opencode-kimi is left as it was (not tested)", kimi.get("ok") is False
          and "not tested yet" in str(kimi.get("state")), kimi)


def t06_three_lenses_reach_the_provider_an_invalid_one_does_not():
    c = Case("chk-f", CODEX_GEMINI, ["codex", "gemini"])
    r1 = c.run("--check", "codex-standards")
    check("[CP1-T06] providers --check codex-standards succeeds", r1.returncode == 0, said(r1))
    check("[CP1-T06] codex-standards sent one test request to the codex fake", len(c.calls("codex")) == 1,
          c.calls("codex"))
    r2 = c.run("--check", "codex-both")
    check("[CP1-T06] providers --check codex-both succeeds", r2.returncode == 0, said(r2))
    check("[CP1-T06] two test requests in total reached the codex fake", len(c.calls("codex")) == 2, c.calls("codex"))
    r3 = c.run("--check", "codex-bogus")
    msg = said(r3)
    check("[CP1-T06] providers --check codex-bogus exits non-zero", r3.returncode != 0, msg)
    check("[CP1-T06] codex-bogus gets the known-providers message naming it and listing codex and gemini",
          "codex-bogus" in msg and re.search(r"\bgemini\b", msg) is not None, msg)
    check("[CP1-T06] the codex log gains no further call", len(c.calls("codex")) == 2, c.calls("codex"))
    check("[CP1-T06] the gemini fake is never called", c.calls("gemini") == [], c.calls("gemini"))


def t07_failing_provider_checked_by_reviewer_name_reports_its_reason():
    c = Case("chk-g", CODEX_GEMINI, ["codex", "gemini"])
    c.fail("gemini")
    r, rows = c.rows("--check", "gemini-correctness")
    check("[CP1-T07] the command still exits 0 (a failing provider is a result, not a crash)", r.returncode == 0,
          said(r))
    row = rows.get("gemini") or {}
    check("[CP1-T07] the gemini row is not ok", bool(row) and row.get("ok") is False, row)
    check("[CP1-T07] its state is a failing result", str(row.get("state", "")).startswith("failing"), row)
    check("[CP1-T07] its state names the fake's 'rate limited' reason", "rate limited" in str(row.get("state")), row)
    check("[CP1-T07] the codex fake receives no call", c.calls("codex") == [], c.calls("codex"))
    check("[CP1-T07] the gemini fake received the test request", len(c.calls("gemini")) >= 1, c.calls("gemini"))


def t08_docs_say_a_reviewer_name_can_be_checked():
    docs = {
        "SKILL.md": ROOT / "skills/ratchet-orchestrator/SKILL.md",
        "schemas.md": ROOT / "skills/ratchet-orchestrator/references/schemas.md",
    }
    describing = 0
    for label, path in docs.items():
        text = path.read_text()
        spots = [m.start() for m in re.finditer(r"--check", text)]
        # `guardrail --check-command` is another command's flag, not providers --check
        spots = [s for s in spots if not text[s:s + 15].startswith("--check-command")
                 and ("providers" in text[max(0, s - 400):s + 400])]
        if not spots:
            continue
        describing += 1
        windows = [text[s:s + 700].lower() for s in spots]
        check(f"[CP1-T08] {label} says providers --check accepts a reviewer name",
              any("reviewer name" in w for w in windows), "no 'reviewer name' near --check")
        check(f"[CP1-T08] {label} gives codex-correctness as an example of such a name",
              any("codex-correctness" in w for w in windows), "no codex-correctness near --check")
        check(f"[CP1-T08] {label} says an unknown name lists the known provider ids",
              any("known provider" in w for w in windows), "no 'known provider' near --check")
    check("[CP1-T08] at least one doc describes providers --check", describing >= 1, describing)


TESTS = [
    ("CP1-T01", t01_reviewer_name_tests_its_provider_and_shows_fresh_result),
    ("CP1-T02", t02_provider_id_check_unchanged),
    ("CP1-T03", t03_all_still_tests_every_installed_provider),
    ("CP1-T04", t04_unknown_name_fails_and_tests_nothing),
    ("CP1-T05", t05_shared_program_tests_only_that_model),
    ("CP1-T06", t06_three_lenses_reach_the_provider_an_invalid_one_does_not),
    ("CP1-T07", t07_failing_provider_checked_by_reviewer_name_reports_its_reason),
    ("CP1-T08", t08_docs_say_a_reviewer_name_can_be_checked),
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
    global _CUR_CASE, FAIL
    for cid, fn in TESTS:
        _CUR_CASE = cid
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash is this case's failed result, never a silent drop
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
