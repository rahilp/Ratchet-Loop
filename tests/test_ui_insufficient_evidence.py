#!/usr/bin/env python3
"""
CP5 tests: undemonstrable cases go back to the capture.   python3 tests/test_ui_insufficient_evidence.py

When a UI reviewer says a case could not be judged from the captured evidence (an `insufficient_evidence` entry
{case, state, missing}, kept apart from `differences`), `ui_review.py combine` must:
  * rank its outcomes: a reviewer's own INVALID first (unchanged); then ANY difference ui-record itself would block on
    -> FAIL with every difference kept unchanged and the unjudged entries listed separately in the combined review's
    `insufficient_evidence`; then any unjudged entry (however malformed) or any capture state marked as a stub
    -> INVALID whose reason starts 'INSUFFICIENT-EVIDENCE:' and sends the reader to the capture spec (states/CPn.json);
    otherwise PASS exactly as before. "Blocks" is ui-record's own test, ui_block_on_nits included.
  * ui-record (read only here) already stops on an INVALID verdict: status 'invalid', exit 2, nothing frozen.

Director requirement, S1..S5 below (extra, unplanned): NO path reaches a passing gate from a stub manifest, and the
guarantee sits on the COMBINE side (any stub the combine step can see is rejected), not on a reviewer choosing to fail it.
How combine learns about stubs is pinned by S2 (CLI `--manifest`), S3 (the run's own states/CPn.json, the file ui-record
hashes) and S5 (any stub value that is not absent/false/null counts).

Method: pure merge cases call the real ui_review.combine (imported with sys.path.insert) and the real command line;
gate cases build a throwaway Ratchet project at the UI gate, run the real `ui_review.py stage`, write the reviewer files
the way the host's reviewer sub-agents do (confirmed through the real guard hook), run the real `ui_review.py combine`
inside the run, then the real `ratchet.py ui-record` and read state.json. No network and no real provider (a fake
`codex` program on a private PATH answers `review`).

Writes a per-test JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP5-T01",
"status": "passed"|"failed", "message": "..."}]}. A case that raises is recorded as failed, never dropped.
"""
from __future__ import annotations
from _docs import README_DOCS  # README.md plus the docs/ pages, read as one text

import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _tmp                                             # noqa: E402
import trust_helpers as th                              # noqa: E402
import test_signing as ts                               # noqa: E402

UIC_SCRIPTS = ROOT / "skills" / "ratchet-ui-compare" / "scripts"
UI_REVIEW = UIC_SCRIPTS / "ui_review.py"
README = README_DOCS
INVENTORY = HERE / "fixtures" / "modularize_cp1" / "test-inventory.md"
THIS = Path(__file__).resolve()

CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
PASS = FAIL = 0
ID_RE = re.compile(r"\[(CP5-[TXS]\d+[a-z]?)\]")
IE_PREFIX = "INSUFFICIENT-EVIDENCE:"
ABSENT = object()
TODAY_KEYS = {"checkpoint", "fingerprint", "compared", "reviewers", "verdict", "invalid_reason", "differences",
              "summary"}


def check(name, cond, detail=""):
    global PASS, FAIL
    cond = bool(cond)
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and cond
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


# ------------------------------------------------------------------------------------------------ review fixtures

def ie(case="UI-C2", state="phone-checkout", missing="no journey steps"):
    return {"case": case, "state": state, "missing": missing}


def diff(did, sev, loc, desc, fixable=True):
    return {"id": did, "severity": sev, "location": loc, "description": desc, "fixable_in_code": fixable}


def rev(role, verdict="PASS", diffs=None, unjudged=ABSENT, **extra):
    r = {"reviewer": role, "verdict": verdict, "differences": list(diffs or [])}
    if unjudged is not ABSENT:
        r["insufficient_evidence"] = unjudged
    r.update(extra)
    return r


V1 = diff("V1", "blocker", "checkout/pay", "Pay button is grey")
V2 = diff("V2", "minor", "checkout/card", "card padding 8px not 16px")
B1 = diff("B1", "major", "checkout/dialog", "focus does not move to dialog")

_scratch = []


def scratch():
    """A folder that is not a project (no git, no .ratchet), used as the working folder for merge calls."""
    if not _scratch:
        _scratch.append(Path(_tmp.mkdtemp(prefix="ratchet-ie-scratch-")))
    return _scratch[0]


def merge(reviews, cp="CP5", compared=None):
    """(result, None) from the real ui_review.combine, or (None, 'ExcType: text') when it raised."""
    if str(UIC_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(UIC_SCRIPTS))
    import ui_review as ur
    old = os.getcwd()
    os.chdir(str(scratch()))
    try:
        got = ur.combine(reviews, cp, "fp-1", compared if compared is not None else
                         {"spec_sha256": "s1", "scope": "checkout"}, check_fp=False)
        return got, None
    except (Exception, SystemExit) as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        os.chdir(old)


def reason(res):
    r = (res or {}).get("invalid_reason")
    return r if isinstance(r, str) else ""


def verdict(res):
    return (res or {}).get("verdict")


def ie_list(res):
    got = (res or {}).get("insufficient_evidence")
    return got if isinstance(got, list) else []


def has_ie_case(res, case):
    return any(isinstance(e, dict) and e.get("case") == case for e in ie_list(res))


def ie_invalid(res, *names):
    """INVALID, reason starts 'INSUFFICIENT-EVIDENCE:' and holds every name."""
    return (verdict(res) == "INVALID" and reason(res).startswith(IE_PREFIX)
            and all(n in reason(res) for n in names))


def diff_ids(res):
    return [d.get("id") for d in (res or {}).get("differences", []) if isinstance(d, dict)]


def stamped(d, role):
    return dict(d, reviewer=role)


# ------------------------------------------------------------------------------------------------ command-line fixtures

def plain_state(name="home", **extra):
    st = {"name": name, "scope": f"The {name} screen", "reference": {}, "app": {}}
    st.update(extra)
    return st


def stub_state(name="stub-UI-C7", case="UI-C7", mark=True):
    return plain_state(name, stub=mark, cases=[case], scope=f"STUB, UNREVIEWED: add the journey for {case}")


class Res:
    pass


_cli_env = []


def cli_env():
    if not _cli_env:
        _cli_env.append(th.make_env("claude"))
    return _cli_env[0]


def cli_combine(visual, behavior, manifest=ABSENT, checkpoint="CP5", order=("visual", "behavior"), extra_inputs=()):
    """`ui_review.py combine` outside a Ratchet run (an ad-hoc, unsigned comparison). manifest: a dict to write and pass
    with --manifest, or ABSENT for no --manifest. Returns a Res with .proc, .comb (the parsed combined file or None)."""
    w = Path(_tmp.mkdtemp(prefix="ratchet-ie-cli-"))
    files = {"visual": w / "visual.json", "behavior": w / "behavior.json"}
    files["visual"].write_text(json.dumps(visual))
    files["behavior"].write_text(json.dumps(behavior))
    cmd = [sys.executable, str(UI_REVIEW), "combine", "--inputs"] + [str(files[r]) for r in order] + list(extra_inputs)
    cmd += ["--out", str(w / "combined.json")]
    if checkpoint:
        cmd += ["--checkpoint", checkpoint]
    if manifest is not ABSENT:
        (w / "manifest.json").write_text(json.dumps(manifest))
        cmd += ["--manifest", str(w / "manifest.json")]
    r = Res()
    r.proc = subprocess.run(cmd, cwd=str(w), env=cli_env(), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            timeout=300)
    r.text = r.proc.stdout + "\n" + r.proc.stderr
    r.path = w / "combined.json"
    try:
        r.comb = json.loads(r.path.read_text())
    except (OSError, ValueError):
        r.comb = None
    return r


def manifest_with(*states, cp="CP5"):
    return {"checkpoint": cp, "spec_sha256": "s1", "review_scope": "all", "states": list(states)}


def not_a_pass(comb):
    """True when no passing combined review came out: no file, or a verdict that is not PASS."""
    return comb is None or verdict(comb) != "PASS"


# ------------------------------------------------------------------------------------------------ in-run fixtures

FAKE_IE_CODEX = r"""out=""
while [ $# -gt 0 ]; do
  case "$1" in --output-last-message) out="$2"; shift 2;; *) shift;; esac
done
if [ ! -t 0 ]; then cat > /dev/null; fi
if [ -n "$STUB_MARK" ]; then : > "$STUB_MARK"; fi
if [ -n "$out" ]; then
  printf '%s' '{"reviewer":"x","verdict":"PASS","differences":[],"insufficient_evidence":[{"case":"UI-C2","state":"phone-checkout","missing":"no journey steps"}]}' > "$out"
fi
"""


def build_project(kind):
    """(d, env): a throwaway Ratchet project whose CP1 has a UI gate and sits at it (behavior passed)."""
    env = th.make_env("claude")
    if kind == "fake":
        env = dict(env, PATH=str(ts.raw_fake_bin("codex", FAKE_IE_CODEX)))
    d = th.cp3_ui_project(env)
    if kind == "nitsoff":
        r = th.rx(d, env, "config", "--set", "ui_block_on_nits=false")
        assert r.returncode == 0, r.stderr + r.stdout
        # a config rule changed after the plan was approved needs the user's yes (the engine's own change check)
        th.rx(d, env, "ask", "approve-changes")
        ts.say_x(d, env, "yes")
        r = th.rx(d, env, "approve-changes")
        assert r.returncode == 0, r.stderr + r.stdout
    th.cp3_pass_behavior(d, env)
    return d, env


PROJECT_KINDS = {"t16": "plain", "t17a": "plain", "t17b": "plain", "t18a": "nitsoff", "t18b": "plain",
                 "t18c": "nitsoff", "t19": "plain", "t20": "plain", "t21": "plain", "t22": "fake", "s2": "plain",
                 "s3": "plain"}
_pool = {}


def start_builds():
    ex = cf.ThreadPoolExecutor(max_workers=4)
    for key, kind in PROJECT_KINDS.items():
        _pool[key] = ex.submit(build_project, kind)
    return ex


def project(key):
    return _pool[key].result() if key in _pool else build_project(PROJECT_KINDS.get(key, "plain"))


def state_of(d):
    return th.cp3_state(d)["checkpoints"]["CP1"]


def ui_gate(d):
    return state_of(d)["gates"]["ui"]


def evidence_combined(d, n=1):
    p = Path(d) / f".ratchet/runs/f/evidence/CP1/ui/round-{n:02d}/combined.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def ui_combine(d, env, visual, behavior, n=1, spec="s1", scope="checkout", states=None, spec_file=None,
               pass_manifest=True, cap=None):
    """The host reviewer route inside a run: write the capture manifest under reviews/CP1/, run the real `stage`, have
    each reviewer 'sub-agent' write its file (confirmed through the real guard hook), then run the real `combine`.
    spec_file: a dict written as the run's states/CP1.json, whose hash becomes the manifest's spec_sha256."""
    run = Path(d) / ".ratchet/runs/f"
    res = Res()
    capdir = run / "reviews" / "CP1" / (cap or f"capture{n}")
    capdir.mkdir(parents=True, exist_ok=True)
    man = capdir / "manifest.json"
    sha = spec
    if spec_file is not None:
        sp = run / "states" / "CP1.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(spec_file, indent=2))
        sha = hashlib.sha256(sp.read_bytes()).hexdigest()
    man.write_text(json.dumps({"checkpoint": "CP1", "spec_sha256": sha,
                               "states": states if states is not None else [plain_state("home")]}))
    res.manifest = man
    res.stage = subprocess.run([sys.executable, str(UI_REVIEW), "stage", "--manifest", str(man), "--scope", scope],
                               cwd=d, capture_output=True, text=True, env=env)
    res.inputs = {}
    try:
        dirs = json.loads(res.stage.stdout)
    except ValueError:
        dirs = {}
    for role, folder in dirs.items():
        f = Path(folder) / "INPUT.md"
        res.inputs[role] = f.read_text() if f.exists() else ""
        shutil.rmtree(folder, ignore_errors=True)
    rd = th.cp3_ui_round_dir(d, n)
    paths = []
    res.guard_ok = True
    for role, body in (("visual", visual), ("behavior", behavior)):
        p = rd / f"{role}.json"
        g = ts.guard(d, env, "Write", {"file_path": str(p)}, agent=f"ratchet-{role}-reviewer")
        res.guard_ok = res.guard_ok and g.returncode == 0
        p.write_text(json.dumps(body))
        paths.append(p)
    res.out = rd / "combined.json"
    cmd = [sys.executable, str(UI_REVIEW), "combine", "--inputs", str(paths[0]), str(paths[1]), "--out", str(res.out),
           "--checkpoint", "CP1"] + (["--manifest", str(man)] if pass_manifest else [])
    res.proc = subprocess.run(cmd, cwd=d, capture_output=True, text=True, env=env)
    res.text = res.proc.stdout + "\n" + res.proc.stderr
    try:
        res.comb = json.loads(res.out.read_text())
    except (OSError, ValueError):
        res.comb = None
    return res


def ui_record(d, env, combined):
    r = th.rx(d, env, "ui-record", "CP1", "--review", str(combined))
    r.text = r.stdout + "\n" + r.stderr
    return r


def blocking_lines(text):
    return [ln.strip() for ln in text.splitlines() if re.match(r"\s*- \[(blocker|major|minor|nit)\]", ln)]


def failed_message(text):
    m = re.search(r"Gate 2 \(UI\) FAILED for CP1: (\d+) blocking difference\(s\)\. Send them to ratchet-implementer", text)
    return m.group(0) if m else None


# ------------------------------------------------------------------------------------------------ pure merge cases

def t01_unjudged_block_and_are_named():
    print("CP5-T01: reviewers who cannot judge cases block the gate and name each one")
    a = ie("UI-C2", "phone-checkout", "no journey steps reach the confirm dialog")
    b = ie("UI-C5", "desktop-empty", "captured at 1280 wide, case needs 390")
    res, err = merge([rev("visual"), rev("behavior", unjudged=[a, b])], cp="CP5")
    check("[CP5-T01] the merge does not raise", res is not None, err)
    check("[CP5-T01] the verdict is INVALID, never PASS", verdict(res) == "INVALID", verdict(res))
    check("[CP5-T01] invalid_reason starts with 'INSUFFICIENT-EVIDENCE:'", reason(res).startswith(IE_PREFIX), reason(res)[:120])
    for needle in ("UI-C2", "phone-checkout", "no journey steps reach the confirm dialog", "UI-C5", "desktop-empty",
                   "captured at 1280 wide, case needs 390"):
        check(f"[CP5-T01] the reason names {needle!r}", needle in reason(res), reason(res)[:300])
    check("[CP5-T01] the reason tells the reader to fix the capture spec states/CP5.json",
          "states/CP5.json" in reason(res) and "capture spec" in reason(res).lower(), reason(res)[:300])
    check("[CP5-T01] differences is empty: nothing exists for the implementer", (res or {}).get("differences") == [],
          (res or {}).get("differences"))
    summ = str((res or {}).get("summary", ""))
    check("[CP5-T01] the summary does not say PASS and counts no unjudged case as a code-fixable difference",
          bool(summ) and "PASS" not in summ and not re.search(r"[1-9]\d* code-fixable", summ), summ)


def t02_difference_plus_unjudged_still_fails():
    print("CP5-T02: a real difference plus an unjudged case still fails, with every difference kept")
    c = ie("UI-C2", "phone-checkout", "no journey steps")
    res, err = merge([rev("visual", "FAIL", [V1, V2]), rev("behavior", "FAIL", [B1], unjudged=[c])])
    check("[CP5-T02] the merge does not raise", res is not None, err)
    check("[CP5-T02] the verdict is FAIL, not INVALID", verdict(res) == "FAIL", verdict(res))
    want = [stamped(V1, "visual"), stamped(V2, "visual"), stamped(B1, "behavior")]
    check("[CP5-T02] differences holds exactly V1, V2, B1 unchanged with their reviewer name",
          (res or {}).get("differences") == want, (res or {}).get("differences"))
    check("[CP5-T02] the combined review carries UI-C2 in its own insufficient_evidence list",
          has_ie_case(res, "UI-C2"), (res or {}).get("insufficient_evidence"))
    check("[CP5-T02] UI-C2 appears nowhere in differences", "UI-C2" not in json.dumps((res or {}).get("differences")),
          diff_ids(res))
    check("[CP5-T02] invalid_reason is empty so the INSUFFICIENT-EVIDENCE path is not taken",
          (res or {}).get("invalid_reason") is None, (res or {}).get("invalid_reason"))


def t03_unjudged_never_change_differences():
    print("CP5-T03: adding unjudged cases never changes the differences that go to the implementer")
    plain, err1 = merge([rev("visual", "FAIL", [V1, V2]), rev("behavior", "FAIL", [B1])])
    withie, err2 = merge([rev("visual", "FAIL", [V1, V2]), rev("behavior", "FAIL", [B1], unjudged=[ie()])])
    check("[CP5-T03] both merges ran", plain is not None and withie is not None, (err1, err2))
    check("[CP5-T03] both verdicts are FAIL", verdict(plain) == "FAIL" and verdict(withie) == "FAIL",
          (verdict(plain), verdict(withie)))
    check("[CP5-T03] the differences lists are byte-for-byte identical JSON",
          json.dumps((plain or {}).get("differences")) == json.dumps((withie or {}).get("differences"))
          and len((plain or {}).get("differences", [])) == 3,
          ((plain or {}).get("differences"), (withie or {}).get("differences")))
    check("[CP5-T03] the result without unjudged entries has exactly today's keys and summary",
          set(plain or {}) == TODAY_KEYS and (plain or {}).get("summary") == "3 code-fixable difference(s) across 2 reviewer(s)",
          (sorted(plain or {}), (plain or {}).get("summary")))
    check("[CP5-T03] the only key the second result adds is insufficient_evidence",
          set(withie or {}) - TODAY_KEYS == {"insufficient_evidence"} and TODAY_KEYS <= set(withie or {}),
          sorted(set(withie or {}) ^ TODAY_KEYS))
    check("[CP5-T03] the second summary still counts the 3 real differences",
          "3 code-fixable difference(s)" in str((withie or {}).get("summary")), (withie or {}).get("summary"))


def t04_unjudged_cannot_downgrade_any_severity():
    print("CP5-T04: an unjudged case cannot downgrade or drop a difference of any severity")
    for sev in ("blocker", "major", "minor", "nit"):
        d1 = diff("D1", sev, "checkout/x", f"a {sev} problem")
        res, err = merge([rev("visual", "FAIL", [d1]), rev("behavior", unjudged=[ie()])])
        check(f"[CP5-T04] ({sev}) the merge does not raise", res is not None, err)
        check(f"[CP5-T04] ({sev}) the verdict is FAIL, never INVALID and never PASS", verdict(res) == "FAIL", verdict(res))
        check(f"[CP5-T04] ({sev}) the difference is kept with its original severity and fixable flag",
              (res or {}).get("differences") == [stamped(d1, "visual")], (res or {}).get("differences"))
        check(f"[CP5-T04] ({sev}) the unjudged entry is listed separately", has_ie_case(res, "UI-C2"),
              (res or {}).get("insufficient_evidence"))
        check(f"[CP5-T04] ({sev}) invalid_reason does not mention INSUFFICIENT-EVIDENCE",
              "INSUFFICIENT-EVIDENCE" not in reason(res), reason(res)[:200])


def t05_one_pass_does_not_cancel_unjudged():
    print("CP5-T05: one reviewer passing does not cancel another reviewer's unjudged case")
    runs = {
        "behaviour self-reports PASS": [rev("visual"), rev("behavior", "PASS", unjudged=[ie()])],
        "behaviour self-reports FAIL with no differences": [rev("visual"), rev("behavior", "FAIL", unjudged=[ie()])],
        "order swapped (PASS)": [rev("behavior", "PASS", unjudged=[ie()]), rev("visual")],
        "order swapped (FAIL)": [rev("behavior", "FAIL", unjudged=[ie()]), rev("visual")],
        "the visual reviewer lists it": [rev("visual", "PASS", unjudged=[ie()]), rev("behavior")],
    }
    for label, reviews in runs.items():
        res, err = merge(reviews)
        check(f"[CP5-T05] ({label}) the merge does not raise", res is not None, err)
        check(f"[CP5-T05] ({label}) not PASS: INVALID with 'INSUFFICIENT-EVIDENCE:' naming UI-C2",
              ie_invalid(res, "UI-C2"), (verdict(res), reason(res)[:160]))


def t06_nothing_unjudged_merges_as_before():
    print("CP5-T06: reviews with nothing unjudged merge exactly as before")
    summary = "0 code-fixable difference(s) across 2 reviewer(s)"

    def as_before(res):
        return (verdict(res) == "PASS" and (res or {}).get("invalid_reason") is None
                and (res or {}).get("differences") == [] and (res or {}).get("summary") == summary
                and set(res or {}) == TODAY_KEYS)
    for label, un in (("absent", ABSENT), ("null", None), ("an empty list", [])):
        res, err = merge([rev("visual"), rev("behavior", unjudged=un)])
        check(f"[CP5-T06] insufficient_evidence {label}: PASS exactly as before", as_before(res),
              (err, verdict(res), sorted(res or {}), (res or {}).get("summary")))
    # the old-style call: four positional arguments, no extra one, default fingerprint check
    if str(UIC_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(UIC_SCRIPTS))
    import ui_review as ur
    old = os.getcwd()
    os.chdir(str(scratch()))
    try:
        now_fp = ur._current_fingerprint() or "fp-1"   # what the default code-version check will compare against
        reviews = [dict(rev("visual"), fingerprint=now_fp), dict(rev("behavior"), fingerprint=now_fp)]
        res = ur.combine(reviews, "CP5", now_fp, {"spec_sha256": "s1", "scope": "checkout"})
        err = None
    except (Exception, SystemExit) as e:
        res, err = None, f"{type(e).__name__}: {e}"
    finally:
        os.chdir(old)
    check("[CP5-T06] the old-style combine(reviews, cp, fingerprint, compared) call does not raise and is PASS as before",
          err is None and as_before(res), (err, sorted(res or {})))
    # manifests with stub false or no stub key, through the real command
    for label, st in (("stub false", plain_state("home", stub=False)), ("no stub key", plain_state("home")),
                      ("stub null", plain_state("home", stub=None))):
        r = cli_combine(rev("visual"), rev("behavior"), manifest_with(st, plain_state("other")))
        comb = dict(r.comb or {})
        comb.pop("unsigned", None)
        check(f"[CP5-T06] a manifest with {label}: combine exits 0 and the result is PASS exactly as before",
              r.proc.returncode == 0 and as_before(comb), (r.proc.returncode, r.proc.stderr[-200:], sorted(comb)))


def t07_reviewer_invalid_keeps_winning():
    print("CP5-T07: a reviewer's own INVALID verdict keeps winning, and unjudged cases are not lost")
    text = "app and reference are in different states"
    res, err = merge([rev("visual", "INVALID", invalid_reason=text), rev("behavior", "PASS", unjudged=[ie()])])
    check("[CP5-T07] the merge does not raise", res is not None, err)
    check("[CP5-T07] the verdict is INVALID, neither PASS nor FAIL", verdict(res) == "INVALID", verdict(res))
    check("[CP5-T07] invalid_reason still holds the visual reviewer's text", text in reason(res), reason(res)[:200])
    check("[CP5-T07] UI-C2 is still carried in the combined unjudged list", has_ie_case(res, "UI-C2"),
          (res or {}).get("insufficient_evidence"))


def t08_same_id_difference_and_unjudged():
    print("CP5-T08: a case id that is both a difference and an unjudged case is reported both ways")
    dup = diff("UI-C2", "blocker", "checkout/total", "Total row is missing")
    res, err = merge([rev("visual", "FAIL", [dup]), rev("behavior", unjudged=[ie("UI-C2", "phone-checkout", "no journey steps")])])
    check("[CP5-T08] the merge does not raise", res is not None, err)
    check("[CP5-T08] the verdict is FAIL", verdict(res) == "FAIL", verdict(res))
    check("[CP5-T08] the difference UI-C2 is still in differences, unchanged",
          (res or {}).get("differences") == [stamped(dup, "visual")], (res or {}).get("differences"))
    got = [e for e in ie_list(res) if isinstance(e, dict) and e.get("case") == "UI-C2"]
    check("[CP5-T08] the unjudged UI-C2 entry is still in the separate list with its state and text",
          len(got) == 1 and got[0].get("state") == "phone-checkout" and got[0].get("missing") == "no journey steps", got)


MALFORMED = [
    ("{}", {}), ("no case", {"state": "s", "missing": "m"}), ("empty case", {"case": "", "missing": "m"}),
    ("case null", {"case": None}), ("case 42", {"case": 42}), ("case a list", {"case": ["UI-C2"]}),
    ("a bare string", "UI-C2"), ("null", None), ("the number 7", 7), ("a list", ["UI-C2"]),
    ("case only", {"case": "UI-C2"}),
]
UNREADABLE = re.compile(r"could not be read|cannot be read|can't be read|unreadable|not readable|malformed", re.I)


def t09_malformed_entry_still_blocks():
    print("CP5-T09: a malformed unjudged entry still blocks the gate")
    for label, entry in MALFORMED:
        res, err = merge([rev("visual"), rev("behavior", unjudged=[entry])])
        check(f"[CP5-T09] ({label}) no exception", res is not None, err)
        check(f"[CP5-T09] ({label}) not PASS: INVALID with 'INSUFFICIENT-EVIDENCE:'", ie_invalid(res), (verdict(res), reason(res)[:160]))
        if label == "case only":
            check(f"[CP5-T09] ({label}) the reason names the usable case UI-C2", "UI-C2" in reason(res), reason(res)[:200])
        else:
            check(f"[CP5-T09] ({label}) the reason says an entry could not be read", bool(UNREADABLE.search(reason(res))),
                  reason(res)[:200])
        with_diff, err2 = merge([rev("visual", "FAIL", [V1]), rev("behavior", unjudged=[entry])])
        check(f"[CP5-T09] ({label}) next to a real difference: FAIL with the difference kept",
              verdict(with_diff) == "FAIL" and (with_diff or {}).get("differences") == [stamped(V1, "visual")],
              (err2, verdict(with_diff), diff_ids(with_diff)))


def t10_unjudged_not_a_list_still_blocks():
    print("CP5-T10: an unjudged list that is not a list still blocks the gate")
    for label, val in (("a string", "UI-C2 not shown"), ("an object", {"case": "UI-C2"}), ("true", True), ("1", 1)):
        res, err = merge([rev("visual"), rev("behavior", unjudged=val)])
        check(f"[CP5-T10] ({label}) no exception", res is not None, err)
        check(f"[CP5-T10] ({label}) INVALID with 'INSUFFICIENT-EVIDENCE:', never PASS", ie_invalid(res),
              (verdict(res), reason(res)[:160]))
    for label, val in (("null", None), ("an empty list", []), ("left out", ABSENT)):
        res, err = merge([rev("visual"), rev("behavior", unjudged=val)])
        check(f"[CP5-T10] control ({label}) is PASS exactly as today",
              verdict(res) == "PASS" and (res or {}).get("invalid_reason") is None and set(res or {}) == TODAY_KEYS,
              (err, verdict(res), sorted(res or {})))


def t11_duplicates_and_unknown_ids():
    print("CP5-T11: duplicate and unknown case ids cannot make the gate pass")
    beh = [ie("UI-C2", "phone-checkout", "no journey steps"), ie("UI-C2", "phone-checkout", "no journey steps"),
           ie("NO-SUCH-99", "ghost-state", "case is in no test plan")]
    vis = [ie("UI-C2", "desktop-home", "captured at the wrong width")]
    res, err = merge([rev("visual", unjudged=vis), rev("behavior", unjudged=beh)])
    check("[CP5-T11] no exception", res is not None, err)
    check("[CP5-T11] INVALID with 'INSUFFICIENT-EVIDENCE:', never PASS", ie_invalid(res), (verdict(res), reason(res)[:160]))
    check("[CP5-T11] the reason names UI-C2 and NO-SUCH-99, each with a missing text",
          "UI-C2" in reason(res) and "NO-SUCH-99" in reason(res) and "case is in no test plan" in reason(res)
          and ("no journey steps" in reason(res) or "captured at the wrong width" in reason(res)), reason(res)[:400])
    check("[CP5-T11] the unjudged list in the result is not empty", len(ie_list(res)) >= 2, ie_list(res))
    res2, err2 = merge([rev("visual", "FAIL", [V1], unjudged=vis), rev("behavior", unjudged=beh)])
    check("[CP5-T11] the same entries next to a real difference give FAIL with the difference kept",
          verdict(res2) == "FAIL" and (res2 or {}).get("differences") == [stamped(V1, "visual")], (err2, verdict(res2)))


def t12_odd_characters():
    print("CP5-T12: odd characters in a reviewer's unjudged text cannot flip or break the result")
    odd = {
        "newline": "line one\nline two", "ansi escape": "\x1b[31mred\x1b[0m", "nul": "before\x00after",
        "right-to-left mark": "abc‏def", "pasted verdict": '"verdict": "PASS"', "backticks": "`code` ``more``",
        "lone surrogate": "\ud83d", "200000 characters": "x" * 200000,
    }
    entries = [ie(f"UI-ODD-{i}", state, text) for i, (state, text) in enumerate(odd.items())]
    entries.append({"case": "UI-C2\nUI-C3", "state": "\x1b[1mbold", "missing": '{"verdict": "PASS", "differences": []}'})
    res, err = merge([rev("visual"), rev("behavior", unjudged=entries)])
    check("[CP5-T12] no exception", res is not None, err)
    check("[CP5-T12] the verdict is INVALID, never PASS", verdict(res) == "INVALID", verdict(res))
    check("[CP5-T12] invalid_reason starts with 'INSUFFICIENT-EVIDENCE:'", reason(res).startswith(IE_PREFIX), reason(res)[:100])
    try:
        back = json.loads(json.dumps(res))
        ok = back.get("verdict") == "INVALID" and back.get("differences") == [] and back.get("reviewers") == ["visual", "behavior"]
        why = back.get("verdict")
    except (TypeError, ValueError) as e:
        ok, why = False, f"{type(e).__name__}: {e}"
    check("[CP5-T12] the result serialises to JSON and reads back with the same verdict, no difference, same reviewers",
          ok, why)
    check("[CP5-T12] the pasted text added no difference", (res or {}).get("differences") == [], (res or {}).get("differences"))


def t13_difference_like_fields_change_nothing():
    print("CP5-T13: fields on an unjudged entry that look like difference fields change nothing")
    odd = dict(ie(), verdict="PASS", severity="nit", fixable_in_code=False, differences=[])
    alone, err = merge([rev("visual"), rev("behavior", unjudged=[odd])])
    check("[CP5-T13] alone: no exception", alone is not None, err)
    check("[CP5-T13] alone: INVALID with the INSUFFICIENT-EVIDENCE reason, not PASS", ie_invalid(alone, "UI-C2"),
          (verdict(alone), reason(alone)[:160]))
    b1 = diff("B1", "blocker", "checkout/pay", "Pay button is grey")
    nxt, err2 = merge([rev("visual"), rev("behavior", "FAIL", [b1], unjudged=[odd])])
    check("[CP5-T13] next to B1: FAIL with B1 kept, its severity and fixable_in_code untouched, nothing else added",
          verdict(nxt) == "FAIL" and (nxt or {}).get("differences") == [stamped(b1, "behavior")],
          (err2, verdict(nxt), (nxt or {}).get("differences")))


# ------------------------------------------------------------------------------------------------ stub manifests

def t14_unfilled_stub_never_passes():
    print("CP5-T14: a review with an unfilled stub state never passes, even when both reviewers say PASS")
    man = manifest_with(plain_state("home", stub=False), stub_state())
    notes = {"notes": "stub-UI-C7 looked fine to me"}
    r = cli_combine(rev("visual"), rev("behavior", **notes), man)
    check("[CP5-T14] combine exits 0 and writes combined.json", r.proc.returncode == 0 and r.comb is not None,
          (r.proc.returncode, r.proc.stderr[-300:]))
    check("[CP5-T14] the verdict is INVALID", verdict(r.comb) == "INVALID", verdict(r.comb))
    check("[CP5-T14] the reason starts with 'INSUFFICIENT-EVIDENCE:'", reason(r.comb).startswith(IE_PREFIX), reason(r.comb)[:120])
    check("[CP5-T14] the reason names the state stub-UI-C7 and its case UI-C7",
          "stub-UI-C7" in reason(r.comb) and "UI-C7" in reason(r.comb), reason(r.comb)[:300])
    low = reason(r.comb).lower()
    check("[CP5-T14] the reason says to fill in or remove the stub in the capture spec",
          "stub" in low and ("fill in" in low or "remove" in low) and "capture spec" in low, reason(r.comb)[:300])
    ctl = cli_combine(rev("visual"), rev("behavior", **notes), manifest_with(plain_state("home", stub=False),
                                                                           plain_state("stub-UI-C7", stub=False, cases=["UI-C7"])))
    check("[CP5-T14] control: the same states with stub false everywhere give PASS", verdict(ctl.comb) == "PASS",
          (ctl.proc.returncode, verdict(ctl.comb), ctl.proc.stderr[-200:]))


def t15_stub_next_to_a_difference_still_fails():
    print("CP5-T15: a stub state next to a real difference still fails with the difference kept")
    man = manifest_with(plain_state("home", stub=False), stub_state())
    r = cli_combine(rev("visual", "FAIL", [V1]), rev("behavior"), man)
    comb = r.comb or {}
    check("[CP5-T15] combine exits 0", r.proc.returncode == 0, r.proc.stderr[-300:])
    check("[CP5-T15] the verdict is FAIL", verdict(comb) == "FAIL", verdict(comb))
    check("[CP5-T15] V1 is in differences unchanged and is the only difference",
          comb.get("differences") == [stamped(V1, "visual")], comb.get("differences"))
    named = [e for e in ie_list(comb) if isinstance(e, dict) and e.get("state") == "stub-UI-C7"]
    check("[CP5-T15] the stub is named in the separate insufficient_evidence list (state stub-UI-C7, case UI-C7)",
          len(named) == 1 and named[0].get("case") == "UI-C7", ie_list(comb))
    check("[CP5-T15] the stub is not in differences and not a code fix", "stub-UI-C7" not in json.dumps(comb.get("differences")),
          comb.get("differences"))


# ------------------------------------------------------------------------------------------------ through ui-record

def t16_unjudged_only_blocked_by_ui_record():
    print("CP5-T16: the unjudged-only review is blocked by the real ui-record without freezing or sending anything to the implementer")
    d, env = project("t16")
    step_before = state_of(d)["step"]
    c = ie("UI-C2", "phone-checkout", "no journey steps")
    res = ui_combine(d, env, rev("visual"), rev("behavior", unjudged=[c]), n=1, spec="s1", scope="checkout")
    check("[CP5-T16] setup: stage and combine ran and the reviewer files were confirmed",
          res.stage.returncode == 0 and res.guard_ok and res.proc.returncode == 0 and res.comb is not None,
          (res.stage.stderr[-200:], res.proc.stderr[-300:]))
    check("[CP5-T16] the signed combined review is INVALID (not PASS)", verdict(res.comb) == "INVALID", verdict(res.comb))
    rec = ui_record(d, env, res.out)
    g = ui_gate(d)
    check("[CP5-T16] ui-record exits 2", rec.returncode == 2, (rec.returncode, rec.text[-300:]))
    check("[CP5-T16] its output holds 'INSUFFICIENT-EVIDENCE:', UI-C2 and 'no journey steps'",
          IE_PREFIX in rec.text and "UI-C2" in rec.text and "no journey steps" in rec.text, rec.text[-400:])
    check("[CP5-T16] its output has no 'Gate 2 (UI) FAILED', no 'Send them to ratchet-implementer' and no blocking line",
          "Gate 2 (UI) FAILED" not in rec.text and "Send them to ratchet-implementer" not in rec.text
          and blocking_lines(rec.text) == [], rec.text[-400:])
    check("[CP5-T16] gates.ui.status is 'invalid', nothing frozen, no blockers set",
          g.get("status") == "invalid" and "frozen" not in g and not g.get("blockers"), g)
    check("[CP5-T16] the checkpoint's step has not moved past the UI step", state_of(d)["step"] == step_before,
          (step_before, state_of(d)["step"]))
    ev = evidence_combined(d, 1)
    check("[CP5-T16] the recorded evidence copy keeps the unjudged entry", has_ie_case(ev, "UI-C2"), ev)
    fixed = ui_combine(d, env, rev("visual"), rev("behavior"), n=2, spec="s2", scope="checkout")
    rec2 = ui_record(d, env, fixed.out)
    check("[CP5-T16] after the capture spec is fixed (changed spec hash, no ui-respec) the next round is accepted and PASSES",
          fixed.proc.returncode == 0 and rec2.returncode == 0 and "Gate 2 (UI) PASSED for CP1" in rec2.text
          and ui_gate(d).get("status") == "passed", (fixed.proc.stderr[-200:], rec2.text[-300:], ui_gate(d)))


def t17_difference_plus_unjudged_through_ui_record():
    print("CP5-T17: a real difference plus an unjudged case, through the real ui-record, fails with every difference sent unchanged")
    pay = diff("V1", "blocker", "checkout/pay", "Pay button is grey")
    card = diff("V2", "minor", "checkout/card", "card padding")
    foc = diff("B1", "major", "checkout/dialog", "focus not in dialog")
    out = {}
    for key, un in (("a", ABSENT), ("b", [ie("UI-C2", "phone-checkout", "no journey steps")])):
        d, env = project("t17" + key)
        res = ui_combine(d, env, rev("visual", "FAIL", [pay, card]), rev("behavior", "FAIL", [foc], unjudged=un), n=1)
        rec = ui_record(d, env, res.out)
        out[key] = (d, env, res, rec)
    for key, (d, env, res, rec) in out.items():
        g = ui_gate(d)
        check(f"[CP5-T17] (project {key.upper()}) setup: combine ran and ui-record exits 2",
              res.proc.returncode == 0 and rec.returncode == 2, (res.proc.stderr[-200:], rec.returncode))
        check(f"[CP5-T17] (project {key.upper()}) the 'Gate 2 (UI) FAILED for CP1: 3 blocking difference(s)' message is printed",
              failed_message(rec.text) is not None and "3 blocking" in rec.text, rec.text[-300:])
        check(f"[CP5-T17] (project {key.upper()}) gates.ui.status 'failed', blockers 3, frozen equals the compared block",
              g.get("status") == "failed" and g.get("blockers") == 3 and g.get("frozen") == (res.comb or {}).get("compared")
              and g.get("frozen"), g)
    (da, ea, ra, reca), (db, eb, rb, recb) = out["a"], out["b"]
    check("[CP5-T17] both projects print the same three '[severity] location: description' lines in the same order",
          blocking_lines(reca.text) == blocking_lines(recb.text) and len(blocking_lines(recb.text)) == 3
          and blocking_lines(recb.text)[0].startswith("- [blocker] checkout/pay"), (blocking_lines(reca.text), blocking_lines(recb.text)))
    check("[CP5-T17] both print the same FAILED message", failed_message(reca.text) == failed_message(recb.text)
          and failed_message(reca.text) is not None, (failed_message(reca.text), failed_message(recb.text)))
    check("[CP5-T17] B never lists UI-C2 as a blocking line and the count is 3, not 4",
          "UI-C2" not in "\n".join(blocking_lines(recb.text)) and "4 blocking" not in recb.text, recb.text[-300:])
    ev = evidence_combined(db, 1)
    check("[CP5-T17] B's evidence combined.json also holds UI-C2 in the separate unjudged list", has_ie_case(ev, "UI-C2"),
          (ev or {}).get("insufficient_evidence"))
    later = ui_combine(db, eb, rev("visual"), rev("behavior"), n=2, spec="s2")
    rec2 = ui_record(db, eb, later.out)
    check("[CP5-T17] in B a later round with a changed spec is refused with the 'ask ui-respec CP1' wording",
          rec2.returncode != 0 and "ask ui-respec CP1" in rec2.text, (rec2.returncode, rec2.text[-300:]))


def t18_unjudged_beside_nit_is_invalid():
    print("CP5-T18: an unjudged case beside only a difference ui-record would not block on is INVALID, never a pass")
    nit = diff("N1", "nit", "header/icon", "icon 1px off")
    un = [ie("UI-C2", "phone-checkout", "no journey steps")]
    d, env = project("t18a")
    step_before = state_of(d)["step"]
    res = ui_combine(d, env, rev("visual", "FAIL", [nit]), rev("behavior", unjudged=un), n=1)
    check("[CP5-T18] with ui_block_on_nits=false the combined review is INVALID with 'INSUFFICIENT-EVIDENCE:' naming UI-C2",
          res.proc.returncode == 0 and ie_invalid(res.comb, "UI-C2"), (res.proc.stderr[-200:], verdict(res.comb), reason(res.comb)[:120]))
    rec = ui_record(d, env, res.out)
    g = ui_gate(d)
    check("[CP5-T18] ui-record exits 2 and prints the INSUFFICIENT-EVIDENCE text",
          rec.returncode == 2 and IE_PREFIX in rec.text and "UI-C2" in rec.text, (rec.returncode, rec.text[-300:]))
    check("[CP5-T18] gates.ui.status is 'invalid' (not passed, not failed), nothing frozen",
          g.get("status") == "invalid" and "frozen" not in g, g)
    check("[CP5-T18] the step has not advanced past the UI gate", state_of(d)["step"] == step_before,
          (step_before, state_of(d)["step"]))
    ev = evidence_combined(d, 1)
    check("[CP5-T18] the nit is still present in differences of the recorded evidence copy",
          isinstance(ev, dict) and [x.get("id") for x in ev.get("differences", [])] == ["N1"], (ev or {}).get("differences"))
    d2, env2 = project("t18b")
    res2 = ui_combine(d2, env2, rev("visual", "FAIL", [nit]), rev("behavior", unjudged=un), n=1)
    rec2 = ui_record(d2, env2, res2.out)
    check("[CP5-T18] control: under the default ui_block_on_nits=true the combined review is FAIL, with the nit as a blocking line",
          verdict(res2.comb) == "FAIL" and rec2.returncode == 2
          and blocking_lines(rec2.text) == ["- [nit] header/icon: icon 1px off"] and ui_gate(d2).get("status") == "failed",
          (verdict(res2.comb), rec2.returncode, blocking_lines(rec2.text), ui_gate(d2).get("status")))
    check("[CP5-T18] control: the FAIL keeps the unjudged entry in its separate list in the evidence copy",
          has_ie_case(evidence_combined(d2, 1), "UI-C2"), evidence_combined(d2, 1))
    d3, env3 = project("t18c")
    res3 = ui_combine(d3, env3, rev("visual", "FAIL", [nit]), rev("behavior"), n=1)
    rec3 = ui_record(d3, env3, res3.out)
    check("[CP5-T18] control: the same nit with no unjudged case and ui_block_on_nits=false records what it does today (passed)",
          rec3.returncode == 0 and "Gate 2 (UI) PASSED for CP1" in rec3.text and ui_gate(d3).get("status") == "passed",
          (rec3.returncode, rec3.text[-200:]))


def t19_blocking_difference_is_not_a_capture_problem():
    print("CP5-T19: a difference ui-record counts as blocking is never turned into a capture problem by an unjudged case")
    missing = diff("V1", "blocker", "checkout/pay", "Pay button missing", fixable=False)
    d, env = project("t19")
    res = ui_combine(d, env, rev("visual", "FAIL", [missing]), rev("behavior", unjudged=[ie()]), n=1)
    check("[CP5-T19] the combined review is FAIL (not INVALID) with the difference kept",
          res.proc.returncode == 0 and verdict(res.comb) == "FAIL"
          and (res.comb or {}).get("differences") == [stamped(missing, "visual")], (res.proc.stderr[-200:], verdict(res.comb)))
    rec = ui_record(d, env, res.out)
    g = ui_gate(d)
    check("[CP5-T19] ui-record exits 2 with 'Gate 2 (UI) FAILED for CP1: 1 blocking difference(s)' and the blocker line",
          rec.returncode == 2 and "Gate 2 (UI) FAILED for CP1: 1 blocking difference(s)" in rec.text
          and blocking_lines(rec.text) == ["- [blocker] checkout/pay: Pay button missing"], (rec.returncode, rec.text[-300:]))
    check("[CP5-T19] the output is not the INSUFFICIENT-EVIDENCE message", IE_PREFIX not in rec.text, rec.text[-300:])
    check("[CP5-T19] gates.ui.status is 'failed' and frozen is set", g.get("status") == "failed" and bool(g.get("frozen")), g)
    check("[CP5-T19] the evidence copy keeps UI-C2 in the separate unjudged list", has_ie_case(evidence_combined(d, 1), "UI-C2"),
          evidence_combined(d, 1))


def t20_clean_review_records_as_before():
    print("CP5-T20: reviews with no unjudged case record exactly as before, through the real ui-record")
    d, env = project("t20")
    res = ui_combine(d, env, rev("visual"), rev("behavior"), n=1)
    rec = ui_record(d, env, res.out)
    g = ui_gate(d)
    check("[CP5-T20] the combined review is PASS", res.proc.returncode == 0 and verdict(res.comb) == "PASS", (res.proc.stderr[-200:], verdict(res.comb)))
    check("[CP5-T20] ui-record exits 0 with 'Gate 2 (UI) PASSED for CP1'", rec.returncode == 0 and "Gate 2 (UI) PASSED for CP1" in rec.text,
          (rec.returncode, rec.text[-200:]))
    check("[CP5-T20] gates.ui.status 'passed', frozen cleared, the step moved on",
          g.get("status") == "passed" and "frozen" not in g and state_of(d)["step"] != "ui", (g, state_of(d)["step"]))


def t21_stub_through_in_run_combine():
    print("CP5-T21: an unfilled stub through the real in-run combine is blocked by ui-record")
    d, env = project("t21")
    step_before = state_of(d)["step"]
    res = ui_combine(d, env, rev("visual"), rev("behavior"), n=1, states=[plain_state("home"), stub_state()])
    check("[CP5-T21] setup: the manifest was stamped and the reviewer files confirmed",
          res.stage.returncode == 0 and res.guard_ok, (res.stage.stderr[-200:]))
    check("[CP5-T21] combine exits 0 and writes a signed combined review", res.proc.returncode == 0
          and isinstance((res.comb or {}).get("signature"), dict), (res.proc.returncode, res.proc.stderr[-300:]))
    check("[CP5-T21] the combined verdict is INVALID and names stub-UI-C7",
          ie_invalid(res.comb, "stub-UI-C7"), (verdict(res.comb), reason(res.comb)[:200]))
    rec = ui_record(d, env, res.out)
    g = ui_gate(d)
    check("[CP5-T21] ui-record exits 2 with 'INSUFFICIENT-EVIDENCE:' and the stub name",
          rec.returncode == 2 and IE_PREFIX in rec.text and "stub-UI-C7" in rec.text, (rec.returncode, rec.text[-300:]))
    check("[CP5-T21] gates.ui.status 'invalid', no frozen value, nothing recorded as passed, step unchanged",
          g.get("status") == "invalid" and "frozen" not in g and "PASSED" not in rec.text
          and state_of(d)["step"] == step_before, (g, state_of(d)["step"]))


def t22_unjudged_list_survives_the_review_step():
    print("CP5-T22: a reviewer's unjudged list survives the real review step")
    d, env = project("t22")
    run = Path(d) / ".ratchet/runs/f"
    capdir = run / "reviews" / "CP1" / "capture"
    capdir.mkdir(parents=True, exist_ok=True)
    man = capdir / "manifest.json"
    man.write_text(json.dumps({"checkpoint": "CP1", "spec_sha256": "s1", "states": [plain_state("home")]}))
    rd = th.cp3_ui_round_dir(d, 1)
    r = subprocess.run([sys.executable, str(UI_REVIEW), "review", "--manifest", str(man), "--out", str(rd), "--scope",
                        "checkout", "--visual", "codex", "--behavior", "codex"], cwd=d, capture_output=True, text=True, env=env)
    roles = {}
    for role in ("visual", "behavior"):
        try:
            roles[role] = json.loads((rd / f"{role}.json").read_text())
        except (OSError, ValueError):
            roles[role] = None
    try:
        comb = json.loads((rd / "combined.json").read_text())
    except (OSError, ValueError):
        comb = None
    check("[CP5-T22] review exits 0 and wrote both role files and combined.json",
          r.returncode == 0 and all(roles.values()) and comb is not None, (r.returncode, r.stderr[-300:]))
    for role, body in roles.items():
        check(f"[CP5-T22] {role}.json keeps the insufficient_evidence entry", has_ie_case(body, "UI-C2"), body)
    check("[CP5-T22] the signed combined.json is INVALID with the INSUFFICIENT-EVIDENCE reason naming UI-C2, although the reviewer said PASS",
          isinstance((comb or {}).get("signature"), dict) and ie_invalid(comb, "UI-C2"), (verdict(comb), reason(comb)[:160]))


def t23_combine_command_says_where_to_fix():
    print("CP5-T23: the combine command tells the reader where to fix an unjudged case")
    a = cli_combine(rev("visual"), rev("behavior", unjudged=[ie()]), checkpoint="CP5")
    check("[CP5-T23] (a) the printed line shows INVALID, names UI-C2 and says to fix the capture spec states/CP5.json",
          a.proc.returncode == 0 and "INVALID" in a.proc.stdout and "UI-C2" in a.proc.stdout and "states/CP5.json" in a.proc.stdout
          and re.search(r"\bPASS\b", a.proc.stdout) is None, a.proc.stdout[-400:])
    b = cli_combine(rev("visual", "FAIL", [V1], unjudged=[ie()]), rev("behavior"), checkpoint="CP5")
    out = b.proc.stdout
    check("[CP5-T23] (b) the printed line shows FAIL and counts only the real difference as code-fixable",
          b.proc.returncode == 0 and "FAIL" in out and "1 code-fixable difference(s)" in out, out[-400:])
    check("[CP5-T23] (b) it lists UI-C2 separately for the capture spec and says a spec change after a failed round goes through `ask ui-respec`",
          "UI-C2" in out and "capture spec" in out.lower() and "ask ui-respec" in out, out[-500:])


def t24_schema():
    print("CP5-T24: reviewers are told about the unjudged list and old reviews still fit the schema")
    if str(UIC_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(UIC_SCRIPTS))
    import ui_review as ur
    schema = ur.REVIEW_SCHEMA
    props = schema.get("properties", {})
    arr = props.get("insufficient_evidence")
    check("[CP5-T24] the schema declares an insufficient_evidence array",
          isinstance(arr, dict) and arr.get("type") == "array", arr)
    item_props = ((arr or {}).get("items") or {}).get("properties", {}) if isinstance(arr, dict) else {}
    check("[CP5-T24] its items name case, state and missing", all(k in item_props for k in ("case", "state", "missing")), item_props)
    check("[CP5-T24] required is still reviewer, verdict, differences (a review without the list stays valid)",
          schema.get("required") == ["reviewer", "verdict", "differences"], schema.get("required"))


def t25_readme():
    print("CP5-T25: the new test file is listed in the README's 'Running the tests' list")
    readme = README.read_text().splitlines()
    start = next((i for i, ln in enumerate(readme) if ln.strip() == "### Running the tests"), None)
    check("[CP5-T25] setup: README has a 'Running the tests' section", start is not None)
    if start is not None:
        end = next((i for i in range(start + 1, len(readme)) if readme[i].startswith("## ") or readme[i].startswith("### ")),
                   len(readme))
        n = [ln.strip() for ln in readme[start:end]].count("python3 tests/test_ui_insufficient_evidence.py")
        check("[CP5-T25] the block holds the line `python3 tests/test_ui_insufficient_evidence.py` exactly once", n == 1, n)
    check("[CP5-T25] this file is tests/test_ui_insufficient_evidence.py",
          THIS.name == "test_ui_insufficient_evidence.py" and THIS.exists())


def t26_inventory():
    print("CP5-T26: the test file matches neither inventory grep, or the inventory names it")
    # The two inventory greps from test-inventory.md, built at run time so this file does not match them itself.
    module_load = "spec_from" + "_file_location"
    source_scan = "|".join(["ENGINE" + r"\.read_text", "as" + "t" + r"\.parse", "as" + "t" + r"\.walk", "get" + "source"])
    src = THIS.read_text()
    hits = [pat for pat in (module_load, source_scan) if re.search(pat, src)]
    inv = INVENTORY.read_text()
    check("[CP5-T26] either no inventory pattern matches this file, or test-inventory.md names it",
          hits == [] or THIS.name in inv, (hits, THIS.name in inv))
    check("[CP5-T26] the inventory rule holds both ways: named exactly when a pattern matches",
          (THIS.name in inv) == bool(hits), (THIS.name in inv, hits))
    g = subprocess.run(["grep", "-lE", module_load + "|" + source_scan, str(THIS)], capture_output=True, text=True, timeout=30)
    check("[CP5-T26] the live grep over this file finds nothing", g.returncode == 1 and g.stdout == "", g.stdout)
    dashes = [c for c in src if c in (chr(0x2014), chr(0x2013))]
    check("[CP5-T26] this file holds no em or en dash", dashes == [], len(dashes))


# ------------------------------------------------------------------------------------------------ director requirement S1..S5

def fresh_repo():
    """A git repo with no Ratchet folder: an ad-hoc comparison."""
    return th.cp5y_repo()


def run_review_outside(manifest, mark):
    """`ui_review.py review` with a fake codex for both roles (it answers PASS, no differences), outside a run."""
    d = fresh_repo()
    env = dict(th.make_env("claude"), PATH=str(ts.raw_fake_bin("codex", FAKE_IE_CODEX.replace(
        ',"insufficient_evidence":[{"case":"UI-C2","state":"phone-checkout","missing":"no journey steps"}]', ""))),
        STUB_MARK=str(mark))
    shots = d / "shots"
    shots.mkdir()
    (shots / "manifest.json").write_text(json.dumps(manifest))
    out = d / "out"
    r = subprocess.run([sys.executable, str(UI_REVIEW), "review", "--manifest", str(shots / "manifest.json"), "--out", str(out),
                        "--scope", "all", "--visual", "codex", "--behavior", "codex"], cwd=d, capture_output=True, text=True,
                       env=env, stdin=subprocess.DEVNULL, timeout=300)
    try:
        comb = json.loads((out / "combined.json").read_text())
    except (OSError, ValueError):
        comb = None
    return r, comb


def s1_direct_review_on_a_stub():
    print("CP5-S1: `review` on a stub manifest never exits 0 and never writes a passing combined.json")
    mark = Path(_tmp.mkdtemp(prefix="ratchet-ie-mark-")) / "started"
    r, comb = run_review_outside(manifest_with(plain_state("home"), stub_state(), cp="CP1"), mark)
    check("[CP5-S1] review exits non-zero (not 0)", r.returncode != 0, (r.returncode, r.stderr[-300:]))
    check("[CP5-S1] no passing combined.json is written", not_a_pass(comb), comb)
    check("[CP5-S1] no reviewer was started", not mark.exists(), "the fake reviewer ran")
    check("[CP5-S1] the refusal names the stub state", "stub-UI-C7" in r.stderr, r.stderr[-300:])


def s2_host_route_with_both_reviewers_passing():
    print("CP5-S2: host reviewer route: stage a stub, both reviewers say PASS, combine is run as SKILL.md runs it")
    d, env = project("s2")
    step_before = state_of(d)["step"]
    res = ui_combine(d, env, rev("visual", notes="stub-UI-C7 looks right"), rev("behavior", notes="stub-UI-C7 looks right"),
                     n=1, states=[plain_state("home"), stub_state()])
    check("[CP5-S2] stage exits 0 and renders the stub as a stub with no evidence for both reviewers (locked CP4-T11)",
          res.stage.returncode == 0 and set(res.inputs) == {"visual", "behavior"}
          and all("STUB, UNREVIEWED" in t and "no evidence" in t.lower() for t in res.inputs.values()),
          (res.stage.stderr[-200:], {k: v[-200:] for k, v in res.inputs.items()}))
    check("[CP5-S2] combine is run with --inputs, --out, --checkpoint and --manifest and exits 0", res.proc.returncode == 0,
          (res.proc.returncode, res.proc.stderr[-300:]))
    check("[CP5-S2] the combined verdict is not PASS: INVALID with 'INSUFFICIENT-EVIDENCE:' naming the stub case",
          ie_invalid(res.comb, "UI-C7", "stub-UI-C7"), (verdict(res.comb), reason(res.comb)[:200]))
    rec = ui_record(d, env, res.out)
    g = ui_gate(d)
    check("[CP5-S2] ui-record on it records no pass: exit 2, status 'invalid', 'PASSED' absent, step unchanged",
          rec.returncode == 2 and g.get("status") == "invalid" and "PASSED" not in rec.text
          and state_of(d)["step"] == step_before, (rec.returncode, g, rec.text[-200:]))


def s3_combine_without_being_handed_the_stub():
    print("CP5-S3: combine rejects a stub it is not told about by flag: no manifest at all, or a manifest that dropped the mark")
    d, env = project("s3")
    stub_only_in_spec = {"checkpoint": "CP1", "states": [
        {"name": "home", "journey": [{"click": "#go"}]}, {"name": "stub-UI-C7", "stub": True, "cases": ["UI-C7"], "journey": []}]}
    # (a) no --manifest: in a run combine refuses outright, so no passing combined.json can come out of it
    a = ui_combine(d, env, rev("visual"), rev("behavior"), n=1, spec_file=stub_only_in_spec, pass_manifest=False,
                   states=[plain_state("home"), plain_state("stub-UI-C7")])
    check("[CP5-S3] (a) combine without --manifest exits non-zero and writes no combined.json",
          a.proc.returncode != 0 and not a.out.exists(), (a.proc.returncode, a.proc.stderr[-200:]))
    # (b) the stamped manifest carries no stub mark, but the run's own states/CP1.json (the file ui-record hashes, and
    # the manifest's spec_sha256 is its hash) holds a stub: combine must read that spec and reject
    b = ui_combine(d, env, rev("visual"), rev("behavior"), n=1, spec_file=stub_only_in_spec,
                   states=[plain_state("home"), plain_state("stub-UI-C7")], cap="capture-b")
    check("[CP5-S3] (b) setup: stage ran and combine exited 0", b.stage.returncode == 0 and b.proc.returncode == 0,
          (b.stage.stderr[-200:], b.proc.stderr[-300:]))
    check("[CP5-S3] (b) the manifest has no stub mark but the run's states/CP1.json does: combine is INVALID naming stub-UI-C7",
          ie_invalid(b.comb, "stub-UI-C7"), (verdict(b.comb), reason(b.comb)[:200]))
    # (c) the spec has no stub either: PASS as before (the spec read adds no false block)
    clean = {"checkpoint": "CP1", "states": [{"name": "home", "journey": [{"click": "#go"}]}]}
    c = ui_combine(d, env, rev("visual"), rev("behavior"), n=1, spec_file=clean, states=[plain_state("home")], cap="capture-c")
    check("[CP5-S3] (c) control: a spec with no stub gives PASS", c.proc.returncode == 0 and verdict(c.comb) == "PASS",
          (c.proc.returncode, c.proc.stderr[-200:], verdict(c.comb)))


def s4_stub_among_real_passing_states():
    print("CP5-S4: a stub that is not the only state still blocks when every real state passes")
    man = manifest_with(plain_state("home", stub=False), plain_state("cart"), stub_state("stub-UI-C7"),
                        plain_state("checkout"), plain_state("done", stub=None))
    r = cli_combine(rev("visual", "PASS"), rev("behavior", "PASS", notes="every state looked right"), man)
    check("[CP5-S4] combine exits 0", r.proc.returncode == 0, r.proc.stderr[-300:])
    check("[CP5-S4] INVALID with 'INSUFFICIENT-EVIDENCE:' naming the one stub state",
          ie_invalid(r.comb, "stub-UI-C7"), (verdict(r.comb), reason(r.comb)[:200]))
    check("[CP5-S4] the reason does not blame the real states", not any(n in reason(r.comb) for n in ("'cart'", "checkout", "'home'", "'done'")),
          reason(r.comb)[:300])
    two = manifest_with(plain_state("home"), stub_state("stub-UI-C7"), stub_state("stub-UI-C9", "UI-C9"))
    r2 = cli_combine(rev("visual"), rev("behavior"), two)
    check("[CP5-S4] with two stubs the reason names both", ie_invalid(r2.comb, "stub-UI-C7", "stub-UI-C9"),
          (verdict(r2.comb), reason(r2.comb)[:200]))


def s5_stub_value_cannot_be_dodged():
    print("CP5-S5: no truthiness bypass: false / absent / null do not block, any other stub value does")
    shot = Path(_tmp.mkdtemp(prefix="ratchet-ie-shots-")) / "tiny.png"
    shot.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
                                   "9c636000000002000154a24f5d0000000049454e44ae426082"))
    with_shots = plain_state("stub-UI-C7", stub=True, cases=["UI-C7"])
    with_shots["reference"] = {"screenshot": str(shot)}
    with_shots["app"] = {"screenshot": str(shot)}
    for label, st, blocks in (
            ("stub false", plain_state("stub-UI-C7", stub=False), False),
            ("no stub key", plain_state("stub-UI-C7"), False),
            ("stub null", plain_state("stub-UI-C7", stub=None), False),
            ("stub true", plain_state("stub-UI-C7", stub=True), True),
            ("stub the string 'true'", plain_state("stub-UI-C7", stub="true"), True),
            ("stub the number 1", plain_state("stub-UI-C7", stub=1), True),
            ("stub true and both screenshots exist", with_shots, True)):
        r = cli_combine(rev("visual"), rev("behavior"), manifest_with(plain_state("home"), st))
        if blocks:
            check(f"[CP5-S5] ({label}) is a stub: INVALID naming it", ie_invalid(r.comb, "stub-UI-C7"),
                  (r.proc.returncode, verdict(r.comb), reason(r.comb)[:160]))
        else:
            check(f"[CP5-S5] ({label}) is not a stub: PASS", r.proc.returncode == 0 and verdict(r.comb) == "PASS",
                  (r.proc.returncode, verdict(r.comb), reason(r.comb)[:160]))
    # the review step refuses any stub value itself (the same rule as combine), before a reviewer starts: it exits
    # non-zero, names the stub, starts no reviewer and writes no passing combined.json
    mark = Path(_tmp.mkdtemp(prefix="ratchet-ie-mark-")) / "started"
    odd = plain_state("stub-UI-C7", stub="true", cases=["UI-C7"])
    r, comb = run_review_outside(manifest_with(plain_state("home"), odd, cp="CP1"), mark)
    check("[CP5-S5] `review` on a manifest whose stub value is the string 'true' never writes a passing combined.json",
          not_a_pass(comb), (r.returncode, verdict(comb)))
    check("[CP5-S5] that review exits non-zero and its refusal names the stub state", r.returncode != 0
          and "stub-UI-C7" in r.stderr, (r.returncode, r.stderr[-300:]))
    check("[CP5-S5] that review started no reviewer", not mark.exists(), "the fake reviewer ran")


# ------------------------------------------------------------------------------------------------ fixer extras X1..X6

def _ur():
    """ui_review, imported once from the skill's scripts folder."""
    if str(UIC_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(UIC_SCRIPTS))
    import ui_review as ur
    return ur


def x1_blocks_matches_the_engine_rule():
    print("CP5-X1: ui_review._blocks, and combine through it, agree with ratchet.py's own _ui_verdict (drift guard)")
    # Mirrors ratchet.py _ui_verdict's per-difference loop: a non-fixable minor or nit is exempt, a nit is exempt when
    # ui_block_on_nits is off, anything else blocks. ui_review._engine() loads ratchet.py as combine and review do.
    ur = _ur()
    eng = ur._engine()
    sevs = ["blocker", "major", "minor", "nit", "BLOCKER", "Nit", " nit", "", None, 3, "critical", ABSENT]
    fixes = [True, False, None, ABSENT, 0, 1, "", "false", []]
    outcomes, bad, n = set(), [], 0
    for sev in sevs:
        for fx in fixes:
            for nits in (True, False):
                d = {"id": "D1", "location": "x", "description": "y"}
                if sev is not ABSENT:
                    d["severity"] = sev
                if fx is not ABSENT:
                    d["fixable_in_code"] = fx
                want = eng._ui_verdict({"differences": [d]}, {"ui_block_on_nits": nits})[0] == "FAIL"
                res = ur.combine([rev("visual", "FAIL", [d], unjudged=[ie()])], "CP5", "fp-1",
                                 {"spec_sha256": "s1", "scope": "all"}, check_fp=False, block_on_nits=nits)
                n += 1
                outcomes.add(want)
                if ur._blocks(d, nits) != want or verdict(res) != ("FAIL" if want else "INVALID"):
                    bad.append((sev, fx, nits, want, verdict(res)))
    check("[CP5-X1] the grid is non-trivial: it holds both blocking and exempt differences",
          n == len(sevs) * len(fixes) * 2 and outcomes == {True, False}, (n, outcomes))
    check("[CP5-X1] _blocks and the unjudged-beside-a-difference verdict match the engine on every combination",
          bad == [], bad[:5])


def x2_capture_stub_mark_follows_is_stub():
    print("CP5-X2: capture.mjs marks a state as a stub under the same rule as ui_review._is_stub")
    import test_ui_capture_stub as cs
    ur = _ur()
    check("[CP5-X2] setup: node is on PATH", cs.have_node())
    cases = [("zero", 0, True), ("empty", "", True), ("string", "true", True), ("one", 1, True), ("true", True, True),
             ("false", False, False), ("null", None, False), ("absent", ABSENT, False)]
    states = []
    for label, val, _ in cases:
        st = cs.real_state(f"s-{label}", [f"CP1-{label}"])
        if val is not ABSENT:
            st["stub"] = val
        states.append(st)
    spec = {"checkpoint": "CP1", "viewport": cs.vp(1280, 800),
            "targets": {"reference": "http://reference.invalid/", "app": "http://app.invalid/"}, "states": states}
    d = cs.fresh_dir("x2-in")
    sp = d / "spec.json"
    sp.write_text(json.dumps(spec))
    p, m = cs.run_capture(sp)
    check("[CP5-X2] capture exits 0 and writes a manifest", p.returncode == 0 and isinstance(m, dict),
          (p.returncode, p.stderr[-300:]))
    got = {s.get("name"): s for s in (m or {}).get("states", [])}
    for (label, val, want), st in zip(cases, states):
        ent = got.get(f"s-{label}", {})
        check(f"[CP5-X2] (stub {val!r}) the manifest entry {'carries' if want else 'has no'} a stub mark",
              bool(ent) and (ent.get("stub") is True) == want, ent)
        check(f"[CP5-X2] (stub {val!r}) Python _is_stub reads the manifest entry as it reads the spec state",
              bool(ent) and ur._is_stub(ent) == ur._is_stub(st) == want, (ur._is_stub(ent), ur._is_stub(st)))


def x3_bare_string_entry_names_its_case():
    print("CP5-X3: a bare string in insufficient_evidence is read as the case id")
    for label, val in (("a list holding one string", ["UI-C2"]), ("a string", "UI-C2")):
        res, err = merge([rev("visual"), rev("behavior", unjudged=val)])
        check(f"[CP5-X3] ({label}) INVALID with 'INSUFFICIENT-EVIDENCE:' naming UI-C2", ie_invalid(res, "UI-C2"),
              (err, verdict(res), reason(res)[:160]))
        check(f"[CP5-X3] ({label}) the reason says 'case UI-C2' and not an unknown case",
              "case UI-C2" in reason(res) and "case ?" not in reason(res), reason(res)[:200])
        check(f"[CP5-X3] ({label}) the combined unjudged list holds the case UI-C2", has_ie_case(res, "UI-C2"), ie_list(res))
        nxt, err2 = merge([rev("visual", "FAIL", [V1]), rev("behavior", unjudged=val)])
        check(f"[CP5-X3] ({label}) next to a real difference: FAIL with the difference kept and UI-C2 listed",
              verdict(nxt) == "FAIL" and diff_ids(nxt) == ["V1"] and has_ie_case(nxt, "UI-C2"), (err2, verdict(nxt)))
    blank, err3 = merge([rev("visual"), rev("behavior", unjudged=["   "])])
    check("[CP5-X3] a blank string still blocks and says it could not be read",
          ie_invalid(blank, "could not be read"), (err3, verdict(blank), reason(blank)[:160]))


def x4_stub_with_several_cases_names_them_all():
    print("CP5-X4: a stub state listing several cases names every one of them")
    man = manifest_with(plain_state("home"), plain_state("stub-multi", stub=True, cases=["UI-C3", "UI-C4", "UI-C5"]))
    r = cli_combine(rev("visual"), rev("behavior"), man)
    check("[CP5-X4] combine exits 0 and is INVALID with 'INSUFFICIENT-EVIDENCE:' naming the state and all three cases",
          r.proc.returncode == 0 and ie_invalid(r.comb, "stub-multi", "UI-C3", "UI-C4", "UI-C5"),
          (r.proc.returncode, verdict(r.comb), reason(r.comb)[:300]))
    named = [e for e in ie_list(r.comb) if isinstance(e, dict) and e.get("state") == "stub-multi"]
    check("[CP5-X4] it is one entry whose case stays the first listed case, UI-C3",
          len(named) == 1 and named[0].get("case") == "UI-C3", ie_list(r.comb))
    check("[CP5-X4] the printed line names the other cases too", "UI-C4" in r.proc.stdout and "UI-C5" in r.proc.stdout,
          r.proc.stdout[-300:])
    one = cli_combine(rev("visual"), rev("behavior"), manifest_with(plain_state("home"), stub_state()))
    check("[CP5-X4] control: a stub with one case reads as before, with no 'stands for' list",
          ie_invalid(one.comb, "stub-UI-C7", "UI-C7") and "stands for" not in reason(one.comb), reason(one.comb)[:300])


LOCKED_WORD = re.compile(r"\blocked\b")


def x5_lock_message_is_honest():
    print("CP5-X5: the capture spec is called locked only when a failed round froze it, and the INVALID round says so too")
    bad_pre = [ie("UI-C2", "phone-checkout", "no journey steps")]
    # outside a Ratchet run nothing is locked: no 'locked' claim, but the ui-respec route is still named for a FAIL
    out_fail = cli_combine(rev("visual", "FAIL", [V1], unjudged=bad_pre), rev("behavior"), checkpoint="CP5").proc.stdout
    check("[CP5-X5] outside a run, a FAIL with unjudged cases claims no lock but still names `ask ui-respec`",
          LOCKED_WORD.search(out_fail) is None and "ask ui-respec CP5" in out_fail and "UI-C2" in out_fail, out_fail[-400:])
    out_inv = cli_combine(rev("visual"), rev("behavior", unjudged=bad_pre), checkpoint="CP5").proc.stdout
    check("[CP5-X5] outside a run, an INVALID with unjudged cases claims no lock", LOCKED_WORD.search(out_inv) is None
          and "states/CP5.json" in out_inv, out_inv[-400:])
    # a reviewer's own INVALID next to an unjudged case still lists the case apart from the reviewer's reason
    rin = cli_combine(rev("visual", "INVALID", invalid_reason="app and reference differ"), rev("behavior", unjudged=bad_pre))
    check("[CP5-X5] a reviewer's own INVALID still prints its unjudged case for the capture spec",
          "UI-C2" in rin.proc.stdout and "capture spec" in rin.proc.stdout, rin.proc.stdout[-300:])
    # inside a run: not frozen yet
    d, env = project("x5")
    only = ui_combine(d, env, rev("visual"), rev("behavior", unjudged=bad_pre), n=1, cap="capture-a")
    check("[CP5-X5] in a run before any failed round, the INVALID reason and the printout claim no lock",
          only.proc.returncode == 0 and ie_invalid(only.comb, "UI-C2") and LOCKED_WORD.search(reason(only.comb)) is None
          and LOCKED_WORD.search(only.proc.stdout) is None, (only.proc.stderr[-200:], reason(only.comb)[:300]))
    first = ui_combine(d, env, rev("visual", "FAIL", [V1]), rev("behavior", unjudged=bad_pre), n=1, cap="capture-b")
    check("[CP5-X5] in a run before it is frozen, a FAIL with unjudged cases claims no lock but names `ask ui-respec CP1`",
          first.proc.returncode == 0 and verdict(first.comb) == "FAIL" and LOCKED_WORD.search(first.proc.stdout) is None
          and "ask ui-respec CP1" in first.proc.stdout, (first.proc.stderr[-200:], first.proc.stdout[-400:]))
    rec = ui_record(d, env, first.out)
    check("[CP5-X5] setup: ui-record records the FAIL and freezes the capture spec", rec.returncode == 2
          and bool(ui_gate(d).get("frozen")), (rec.returncode, ui_gate(d)))
    # inside a run, frozen by that FAIL: the next round says the spec is locked, in both outcomes
    nxt = ui_combine(d, env, rev("visual"), rev("behavior", unjudged=bad_pre), n=2)
    check("[CP5-X5] frozen: an unjudged-only round is INVALID and its reason says the spec is locked and names `ask ui-respec CP1`",
          nxt.proc.returncode == 0 and ie_invalid(nxt.comb, "UI-C2") and LOCKED_WORD.search(reason(nxt.comb)) is not None
          and "ask ui-respec CP1" in reason(nxt.comb), (nxt.proc.stderr[-200:], reason(nxt.comb)[:400]))
    check("[CP5-X5] frozen: the printed output says the same", LOCKED_WORD.search(nxt.proc.stdout) is not None
          and "ask ui-respec CP1" in nxt.proc.stdout, nxt.proc.stdout[-400:])
    again = ui_combine(d, env, rev("visual", "FAIL", [V1]), rev("behavior", unjudged=bad_pre), n=2, cap="capture-b")
    check("[CP5-X5] frozen: a FAIL with unjudged cases says the capture spec is locked and names `ask ui-respec CP1`",
          again.proc.returncode == 0 and verdict(again.comb) == "FAIL" and "The capture spec is locked" in again.proc.stdout
          and "ask ui-respec CP1" in again.proc.stdout, (again.proc.stderr[-200:], again.proc.stdout[-400:]))


def x6_schema_requires_case_and_missing():
    print("CP5-X6: an insufficient_evidence item must carry a case and what is missing, the review's own required list is unchanged")
    schema = _ur().REVIEW_SCHEMA
    item = (schema.get("properties", {}).get("insufficient_evidence") or {}).get("items") or {}
    check("[CP5-X6] each item requires case and missing", item.get("required") == ["case", "missing"], item.get("required"))
    check("[CP5-X6] the review itself still requires reviewer, verdict and differences only",
          schema.get("required") == ["reviewer", "verdict", "differences"], schema.get("required"))


def x7_frozen_spec_through_review_and_invalid():
    print("CP5-X7: in a run with a frozen capture spec, `review` and a reviewer's own INVALID both say the spec is locked")
    d, env = build_project("fake")  # the fake codex answers PASS with one unjudged case, UI-C2
    bad = [ie("UI-C2", "phone-checkout", "no journey steps")]
    first = ui_combine(d, env, rev("visual", "FAIL", [V1]), rev("behavior", unjudged=bad), n=1, cap="capture-b")
    rec = ui_record(d, env, first.out)
    check("[CP5-X7] setup: a FAIL with an unjudged case is recorded and freezes the capture spec",
          first.proc.returncode == 0 and rec.returncode == 2 and bool(ui_gate(d).get("frozen")), (rec.returncode, ui_gate(d)))
    capdir = Path(first.manifest).parent.parent / "capture-r"
    capdir.mkdir(parents=True, exist_ok=True)
    man = capdir / "manifest.json"
    man.write_text(json.dumps({"checkpoint": "CP1", "spec_sha256": "s1", "states": [plain_state("home")]}))
    rd = th.cp3_ui_round_dir(d, 2)
    r = subprocess.run([sys.executable, str(UI_REVIEW), "review", "--manifest", str(man), "--out", str(rd), "--scope",
                        "checkout", "--visual", "codex", "--behavior", "codex"], cwd=d, capture_output=True, text=True, env=env)
    try:
        comb = json.loads((rd / "combined.json").read_text())
    except (OSError, ValueError):
        comb = None
    check("[CP5-X7] review in the frozen run is INVALID and its reason says the spec is locked and names `ask ui-respec CP1`",
          r.returncode == 0 and ie_invalid(comb, "UI-C2") and LOCKED_WORD.search(reason(comb)) is not None
          and "ask ui-respec CP1" in reason(comb), (r.returncode, r.stderr[-200:], reason(comb)[:400]))
    check("[CP5-X7] review prints the same, and the lock sentence starts with a capital letter",
          LOCKED_WORD.search(r.stdout) is not None and "ask ui-respec CP1" in r.stdout
          and "code. The capture spec is locked" in reason(comb) and ". the capture spec" not in reason(comb), r.stdout[-400:])
    inv = ui_combine(d, env, rev("visual", "INVALID", invalid_reason="app and reference differ"), rev("behavior", unjudged=bad), n=2)
    check("[CP5-X7] frozen: a reviewer's own INVALID beside an unjudged case still prints the lock note and `ask ui-respec CP1`",
          inv.proc.returncode == 0 and verdict(inv.comb) == "INVALID" and "UI-C2" in inv.proc.stdout
          and LOCKED_WORD.search(inv.proc.stdout) is not None and "ask ui-respec CP1" in inv.proc.stdout, inv.proc.stdout[-400:])


# ------------------------------------------------------------------------------------------------ run everything

TESTS = [
    ("CP5-T01", t01_unjudged_block_and_are_named), ("CP5-T02", t02_difference_plus_unjudged_still_fails),
    ("CP5-T03", t03_unjudged_never_change_differences), ("CP5-T04", t04_unjudged_cannot_downgrade_any_severity),
    ("CP5-T05", t05_one_pass_does_not_cancel_unjudged), ("CP5-T06", t06_nothing_unjudged_merges_as_before),
    ("CP5-T07", t07_reviewer_invalid_keeps_winning), ("CP5-T08", t08_same_id_difference_and_unjudged),
    ("CP5-T09", t09_malformed_entry_still_blocks), ("CP5-T10", t10_unjudged_not_a_list_still_blocks),
    ("CP5-T11", t11_duplicates_and_unknown_ids), ("CP5-T12", t12_odd_characters),
    ("CP5-T13", t13_difference_like_fields_change_nothing), ("CP5-T14", t14_unfilled_stub_never_passes),
    ("CP5-T15", t15_stub_next_to_a_difference_still_fails), ("CP5-T16", t16_unjudged_only_blocked_by_ui_record),
    ("CP5-T17", t17_difference_plus_unjudged_through_ui_record), ("CP5-T18", t18_unjudged_beside_nit_is_invalid),
    ("CP5-T19", t19_blocking_difference_is_not_a_capture_problem), ("CP5-T20", t20_clean_review_records_as_before),
    ("CP5-T21", t21_stub_through_in_run_combine), ("CP5-T22", t22_unjudged_list_survives_the_review_step),
    ("CP5-T23", t23_combine_command_says_where_to_fix), ("CP5-T24", t24_schema), ("CP5-T25", t25_readme),
    ("CP5-T26", t26_inventory),
    ("CP5-S1", s1_direct_review_on_a_stub), ("CP5-S2", s2_host_route_with_both_reviewers_passing),
    ("CP5-S3", s3_combine_without_being_handed_the_stub), ("CP5-S4", s4_stub_among_real_passing_states),
    ("CP5-S5", s5_stub_value_cannot_be_dodged),
]


TESTS += [
    ("CP5-X1", x1_blocks_matches_the_engine_rule), ("CP5-X2", x2_capture_stub_mark_follows_is_stub),
    ("CP5-X3", x3_bare_string_entry_names_its_case), ("CP5-X4", x4_stub_with_several_cases_names_them_all),
    ("CP5-X5", x5_lock_message_is_honest), ("CP5-X6", x6_schema_requires_case_and_missing),
    ("CP5-X7", x7_frozen_spec_through_review_and_invalid),
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
    pool = start_builds()
    for cid, fn in TESTS:
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Exception as e:  # a crash still counts as a failed result for this case, never a silent drop
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"crashed: {type(e).__name__}: {e}")
            print(f"  ✗ [{cid}] CRASHED: {type(e).__name__}: {e}")
            traceback.print_exc()
    pool.shutdown(wait=False)
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
