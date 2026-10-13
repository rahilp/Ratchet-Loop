#!/usr/bin/env python3
"""
Plain-language status tests (CP3): `status` (text by default, JSON behind --json), `plan-summary`, blocked-run
options, jargon-free user-visible text, and the hooks-skipped line.
    python3 tests/test_status_plain.py

Assumed interface (the implementation must provide it):
  * `status`            plain text: first line "Step N of M done: <title of last finished step>" (no flag)
  * `status --json`     the old machine output, unchanged
  * `plan-summary`      new command: one block per step with its title, first done-when line, a "UI review: yes|no"
                        line, "Risk: low|normal|high" and the reviewer ids for that risk; a note when hooks are skipped
  * blocked `status`    the reason in quotes plus 2-3 options on lines starting "A)", "B)", ("C)")
  * blocked `next`      still JSON (phase, action, stop_ok) plus "options": [{"letter": "A", "text": "..."}]
Everything runs in throwaway git projects with a temp HOME / RATCHET_HOME; the repo's own .ratchet is never touched.
"""
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import _tmp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
SCRUB = ("RATCHET_ROLE", "RATCHET_HUMAN_OVERRIDE", "RATCHET_ROOT", "RATCHET_HOST_PROVIDER", "CLAUDECODE",
         "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_SANDBOX_NETWORK_DISABLED",
         "GEMINI_CLI", "COPILOT_CLI", "GITHUB_COPILOT_CLI", "CODEX_HOME", "COPILOT_HOME", "RATCHET_HOOKS_WAIVE")
HOME = Path(_tmp.mkdtemp(prefix="ratchet-sp-home-"))


def path_without_agent_clis() -> str:
    """The real PATH minus every agent CLI the engine knows, so a machine with Codex or Gemini installed
    sees the same 'no other provider' plan as one without them."""
    spec = importlib.util.spec_from_file_location("ratchet_engine_sp", ENGINE)
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    agents = {pr["bin"].lower() for pr in eng.PRESETS.values()}

    def is_agent(name):  # on Windows, codex.cmd or gemini.exe is still codex or gemini
        return (os.path.splitext(name)[0] if os.name == "nt" else name).lower() in agents

    folders = []
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        names = os.listdir(folder) if os.path.isdir(folder) else []
        if not any(is_agent(n) for n in names):
            folders.append(folder)  # most folders hold no agent CLI and are kept as they are
            continue
        # a folder that holds one (a mise or npm bin, say) is replaced by a copy of its other tools
        shadow = Path(_tmp.mkdtemp(prefix="ratchet-sp-bin-"))
        for name in names:
            src = Path(folder) / name
            if not is_agent(name) and src.is_file():
                try:
                    (shadow / name).symlink_to(src)
                except OSError:  # no symlinks allowed (stock Windows): that one tool is left out
                    pass
        folders.append(str(shadow))
    return os.pathsep.join(folders)


ENV = {k: v for k, v in os.environ.items() if k not in SCRUB}
ENV.update(HOME=str(HOME), RATCHET_HOME=str(HOME / "rh"), CODEX_HOME=str(HOME / "codexhome"),
           COPILOT_HOME=str(HOME / "copilothome"), RATCHET_HOST_PROVIDER="anthropic", PATH=path_without_agent_clis())
TRANSCRIPTS = Path(_tmp.mkdtemp(prefix="ratchet-sp-transcripts-"))
PASS = FAIL = 0
GO = "yes, go ahead without hooks"
JARGON = re.compile(r"\b(fingerprint|lens|slot|nonce|adversarial|director|approve-changes|gate|stop_blocks|round cap)\b", re.I)
RAW_PHASE = re.compile(r"\b(awaiting|planning)_[a-z_]+\b")
TRACE = "Traceback"

TITLES = ["Sign-up form", "Login page", "Password reset", "Profile page", "Email alerts", "Admin report"]
DONE_WHEN = ["Visitors can create an account", "Members can sign in", "A forgotten password can be reset",
             "Members can edit their profile", "Members get an email when something changes",
             "Admins can download a monthly report"]
RISKS = ["low", "normal", "normal", "high", "low", "normal"]
UI = [False, True, False, False, True, False]
KEEP = []


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:300]}"))


def rx(d, *args, env=None):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=d, capture_output=True, text=True,
                          env=env or ENV, timeout=120)


def git(d, *a):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True)


def say(d, text, session="s-owner"):
    tp = TRANSCRIPTS / f"{Path(d).name}-{session}.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    return subprocess.run([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, env=ENV,
                          input=json.dumps({"cwd": str(d), "prompt": text, "session_id": session,
                                            "transcript_path": str(tp)}))


def clear_hb(d):
    p = Path(d) / ".ratchet" / "hook-heartbeat.json"
    if p.exists():
        hb = json.loads(p.read_text())
        for k in ("guard", "stop"):
            hb.pop(k, None)
        p.write_text(json.dumps(hb))


def new_git_dir(prefix) -> Path:
    d = Path(_tmp.mkdtemp(prefix=prefix))
    KEEP.append(d)
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(d, *c)
    (d / "src").mkdir()
    (d / "tests").mkdir()
    (d / "src/a.js").write_text("export const a = 1;\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    return d


def plan_project(approve: bool) -> Path:
    """A 6-step plan (mixed risk, two UI steps). Submitted; approved when `approve`."""
    d = new_git_dir("ratchet-sp-p-")
    say(d, "please build the feature")
    r = rx(d, "init", "feature", "--slug", "f")
    assert r.returncode == 0, r.stderr + r.stdout
    rx(d, "config", "--set", "min_review_seconds=0")
    run = d / ".ratchet/runs/f"
    (run / "app-scout.json").write_text("{}")
    plan = {"feature": "Accounts", "objective": _tmp.OBJECTIVE_MIN, "checkpoints": []}
    for i in range(6):
        cp = {"id": f"CP{i + 1}", "title": TITLES[i], "summary": "s", "done_when": [DONE_WHEN[i], "second line"],
              "gates": {"behavior": True, "ui": UI[i], "adversarial": True}, "risk": RISKS[i],
              "tests": {"files": [f"tests/cp{i + 1}.test.js"], "command": f"node --test tests/cp{i + 1}.test.js"}}
        if RISKS[i] == "high":
            cp["risk_reason"] = _tmp.RISK_REASON_MIN  # CP1: plan-submit refuses high risk with no risk_reason
        if UI[i]:
            cp.update(ui_scope="the page", ui_states=[{"name": "default", "how": "open it"}])
        plan["checkpoints"].append(cp)
        (run / "tests" / f"CP{i + 1}.json").write_text(json.dumps({"cases": [{"id": f"CP{i + 1}-T01", "layer": "code", "proves": [1]}]}))
    (run / "checkpoints.json").write_text(json.dumps(plan))
    r = rx(d, "plan-submit")
    assert r.returncode == 0, r.stderr + r.stdout
    if approve:
        rx(d, "ask", "approve-plan")  # CP5: an approval answers the question Ratchet printed
        say(d, "yes, go ahead")
        r = rx(d, "approve-plan")
        assert r.returncode == 0, r.stderr + r.stdout
    clear_hb(d)
    return d


def clone(src: Path) -> Path:
    dst = Path(_tmp.mkdtemp(prefix="ratchet-sp-c-"))
    KEEP.append(dst)
    shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return dst


def spath(d):
    return Path(d) / ".ratchet/runs/f/state.json"


def mutate(d, fn):
    """Edit the throwaway project's state.json (fixture only) and re-baseline its integrity record."""
    p = spath(d)
    st = json.loads(p.read_text())
    fn(st)
    p.write_text(json.dumps(st))
    # Re-baseline with the engine's own record_chain (what accept-state does), in this suite's RATCHET_HOME.
    # accept-state itself now needs a person typing a code at a real terminal, which a fixture cannot be.
    r = subprocess.run([sys.executable, "-c", REBASELINE, str(ENGINE), str(p)], cwd=d, env=ENV,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    return d


REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
              "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
              "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
              "m.record_chain(Path(sys.argv[2]))\n")


def finish(st, n):
    for i in range(1, 7):
        c = st["checkpoints"]["CP%d" % i]
        c["status"] = "done" if i <= n else "pending"
        c["step"] = "complete" if i <= n else "tests"
        for g in c["gates"].values():
            if g.get("required") or g is c["gates"].get("human"):
                g["status"] = "passed" if i <= n else "pending"
    st["current"] = "CP%d" % (n + 1) if n < 6 else None


def blocked(reason):
    def f(st):
        st["blocked_from"] = st["phase"]
        st["phase"] = "blocked"
        st["blocked_reason"] = reason
    return f


def phase(name, **extra):
    def f(st):
        st["phase"] = name
        st.update(extra)
    return f


def status_text(d):
    return rx(d, "status")


def opts_in(text):
    return re.findall(r"^\s*([A-Z])\)\s+(\S.*)$", text, re.M)


def quoted(text, s):
    return any(q1 + s + q2 in text for q1, q2 in (('"', '"'), ("“", "”"), ("'", "'")))


def blocks_by_title(text):
    """Split plan-summary text into one block per step: from the line naming its title to the next title's line."""
    lines = text.splitlines()
    idx = []
    for i, t in enumerate(TITLES):
        hits = [n for n, l in enumerate(lines) if t in l]
        idx.append(hits[0] if len(hits) == 1 else None)
    if None in idx or idx != sorted(idx):
        return None, [sum(1 for l in lines if t in l) for t in TITLES]
    idx.append(len(lines))
    return ["\n".join(lines[idx[i]:idx[i + 1]]) for i in range(6)], None


def main():
    base = plan_project(True)
    pre = plan_project(False)

    # ------------------------------------------------------------ T01 / T02 / T03
    building = clone(base)
    mutate(building, lambda st: finish(st, 2))
    print("[CP3-T01] status is a short plain summary by default")
    r = status_text(building)
    out = r.stdout
    try:
        json.loads(out)
        is_json = True
    except ValueError:
        is_json = False
    first = out.strip().splitlines()[0] if out.strip() else ""
    check("[CP3-T01] exit 0 and not JSON", r.returncode == 0 and not is_json, r.stderr + out[:100])
    check("[CP3-T01] first line is 'Step 2 of 6 done: <title of the last finished step>'",
          first.startswith("Step 2 of 6 done:") and TITLES[1] in first, first)
    check("[CP3-T01] at most 12 lines", 0 < len(out.strip().splitlines()) <= 12, len(out.splitlines()))
    check("[CP3-T01] says what is happening now, by the current step's title", TITLES[2] in out, out)

    print("[CP3-T02] status --json keeps the machine output")
    r = rx(building, "status", "--json")
    try:
        j = json.loads(r.stdout)
    except ValueError:
        j = None
    want = {"phase": str, "current": str, "next": str, "stop_ok": bool, "checkpoints": list, "viewer_url": str,
            "root": str, "slug": str, "feature": str, "done": int, "total": int, "hooks": dict}
    check("[CP3-T02] valid JSON object", isinstance(j, dict), r.stdout[:100] + r.stderr)
    j = j or {}
    check("[CP3-T02] pre-existing keys keep their types", all(isinstance(j.get(k), t) for k, t in want.items()),
          {k: type(j.get(k)).__name__ for k in want})
    check("[CP3-T02] hooks_warning key is still there", "hooks_warning" in j)
    check("[CP3-T02] values are unchanged (phase building, current CP3, 2 of 6)",
          j.get("phase") == "building" and j.get("current") == "CP3" and (j.get("done"), j.get("total")) == (2, 6), j)

    print("[CP3-T03] progress count at the boundaries")
    zero = clone(base)
    one = clone(base)
    mutate(one, lambda st: finish(st, 1))
    alldone = clone(base)
    mutate(alldone, lambda st: (finish(st, 6), st.update(phase="done")))
    z, o, a = status_text(zero).stdout, status_text(one).stdout, status_text(alldone).stdout
    check("[CP3-T03] 0 done: 'Step 0 of 6 done' or 'Not started'",
          "Step 0 of 6 done" in z or "not started" in z.lower(), z)
    check("[CP3-T03] 1 done: first line 'Step 1 of 6 done: <title>'",
          o.strip().splitlines()[0].startswith("Step 1 of 6 done:") and TITLES[0] in o.splitlines()[0], o)
    check("[CP3-T03] all done: says all 6 steps are done, never 'Step 7 of 6'",
          re.search(r"all 6 steps", a, re.I) is not None and "Step 7" not in a, a)

    # ------------------------------------------------------------ T04
    print("[CP3-T04] status says the right thing for every phase")
    variants = {
        "planning": (clone(base), phase("planning")),
        "awaiting_plan_approval": (clone(pre), None),
        "awaiting_checkpoint_review": (clone(base), lambda st: (finish(st, 2), st.update(phase="awaiting_checkpoint_review", review_target="CP2"))),
        "paused": (clone(base), lambda st: st.update(paused_from="building", phase="paused", blocked_reason="paused")),
        "cancelled": (clone(base), lambda st: st.update(phase="cancelled", blocked_reason="cancelled by the user")),
        "done": (clone(base), lambda st: (finish(st, 6), st.update(phase="done"))),
    }
    outs = {}
    for name, (d, fn) in variants.items():
        if fn:
            mutate(d, fn)
        r = status_text(d)
        outs[name] = r.stdout
        check(f"[CP3-T04] {name}: exit 0, no traceback, no raw phase identifier",
              r.returncode == 0 and TRACE not in r.stderr and not RAW_PHASE.search(r.stdout), r.stderr + r.stdout)
    check("[CP3-T04] every phase prints something different", len(set(outs.values())) == len(outs), list(outs))
    check("[CP3-T04] plan approval waits for the user's yes",
          re.search(r"plan", outs["awaiting_plan_approval"], re.I) and re.search(r"approv|yes|your", outs["awaiting_plan_approval"], re.I),
          outs["awaiting_plan_approval"])
    check("[CP3-T04] paused says paused", "paused" in outs["paused"].lower(), outs["paused"])
    check("[CP3-T04] cancelled says cancelled", "cancel" in outs["cancelled"].lower(), outs["cancelled"])
    check("[CP3-T04] review phase asks the user to look at the step",
          re.search(r"review|look|try", outs["awaiting_checkpoint_review"], re.I) is not None, outs["awaiting_checkpoint_review"])

    # ------------------------------------------------------------ T05
    print("[CP3-T05] no plan yet, and outside a run")
    fresh = new_git_dir("ratchet-sp-fresh-")
    say(fresh, "please build the feature")
    rx(fresh, "init", "feature", "--slug", "f")
    r = status_text(fresh)
    check("[CP3-T05] fresh project: exit 0, no traceback, plain 'no plan yet' line",
          r.returncode == 0 and TRACE not in r.stderr and re.search(r"no plan (yet)?", r.stdout, re.I) is not None,
          r.stderr + r.stdout)
    bare = new_git_dir("ratchet-sp-bare-")
    r = status_text(bare)
    check("[CP3-T05] never-initialised dir: non-zero, actionable message, no traceback",
          r.returncode != 0 and TRACE not in r.stderr and TRACE not in r.stdout and (r.stderr + r.stdout).strip() != "",
          r.stderr + r.stdout)

    # ------------------------------------------------------------ T06 / T07 / T08
    print("[CP3-T06] plan summary lists every step")
    r = rx(pre, "plan-summary")
    check("[CP3-T06] plan-summary exits 0", r.returncode == 0, r.stderr + r.stdout)
    blks, counts = blocks_by_title(r.stdout)
    check("[CP3-T06] each of the six titles appears on exactly one line, in plan order", blks is not None, counts)
    blks = blks or [""] * 6
    check("[CP3-T06] each step shows its first done-when line", all(DONE_WHEN[i] in blks[i] for i in range(6)),
          [DONE_WHEN[i] in blks[i] for i in range(6)])
    ui_marks = [re.search(r"UI review\W+(yes|no)\b", b, re.I) for b in blks]
    check("[CP3-T06] each step states 'UI review: yes|no' matching the plan",
          all(m and (m.group(1).lower() == "yes") == UI[i] for i, m in enumerate(ui_marks)),
          [m.group(1) if m else None for m in ui_marks])
    check("[CP3-T06] each step states its risk in words",
          all(re.search(r"risk\W+" + RISKS[i] + r"\b", blks[i], re.I) for i in range(6)), [b[:120] for b in blks])

    print("[CP3-T07] plan summary reflects per-step differences")
    depth = json.loads(rx(pre, "config").stdout)["review_depth"]

    def reviewed_by(block):
        m = re.search(r"Reviewed by: (.*)", block)
        return [x.strip() for x in m.group(1).split(";")] if m else []
    low, normal, high = reviewed_by(blks[0]), reviewed_by(blks[1]), reviewed_by(blks[3])
    check("[CP3-T07] low-risk step names its one reviewer in plain words",
          len(low) == 1 and "(code standards and bugs)" in low[0], low)
    check("[CP3-T07] high-risk step lists more reviewers than the low-risk one, incl. one on bugs",
          len(high) == len(depth["high"]) > len(low) and any("(bugs and security)" in h for h in high), high)
    check("[CP3-T07] normal-risk step differs from both", normal not in (low, high) and len(normal) == len(depth["normal"]),
          normal)
    check("[CP3-T07] no reviewer ids (host-both, codex-correctness) or arrows in the summary",
          not re.search(r"host-|-correctness|-standards|any-|→", "\n".join(blks)), blks[3])
    check("[CP3-T07] the summary names the models and warns when no other provider can review",
          "Models:" in r.stdout and "no other provider is available" in r.stdout, r.stdout[-600:])
    check("[CP3-T07] UI review says yes for exactly the two UI steps",
          [bool(m and m.group(1).lower() == "yes") for m in ui_marks] == UI, UI)

    print("[CP3-T08] plan summary edge cases")
    r = rx(fresh, "plan-summary")
    check("[CP3-T08] no plan: non-zero, message, no traceback",
          r.returncode != 0 and TRACE not in r.stderr and (r.stderr + r.stdout).strip() != "", r.stderr + r.stdout)
    waived = clone(base)
    rx(waived, "ask", "hooks-waive")
    say(waived, GO)
    rx(waived, "hooks-waive")
    clear_hb(waived)
    r = rx(waived, "plan-summary")
    b2, _ = blocks_by_title(r.stdout)
    check("[CP3-T08] waived: still lists all six steps", r.returncode == 0 and b2 is not None, r.stderr + r.stdout[:200])
    check("[CP3-T08] waived: one line notes that hooks were skipped",
          len([l for l in r.stdout.splitlines() if re.search(r"hooks", l, re.I) and re.search(r"skipped", l, re.I)]) == 1,
          r.stdout)
    r = rx(pre, "plan-summary")
    check("[CP3-T08] not waived: no 'skipped' note", "skipped" not in r.stdout.lower(), r.stdout)

    # ------------------------------------------------------------ T09 / T10 / T11 / T12
    print("[CP3-T09] a blocked run quotes the finding and offers lettered options")
    R_BEH = "CP3: behavior gate failed 3 times"
    P_BEH = "The tests for CP3 still fail after 3 tries."
    P_ADV = "The reviewers still have 2 open concern(s) about CP3 after 6 rounds of fixes, which is the limit."
    P_CAP = "The reviewers have used all the review rounds allowed for this run (30 of 30)."
    R_ADV = "CP3: adversarial review still has 2 finding(s) after 6 rounds - needs a human decision."
    R_CAP = "Run-wide adversarial round cap reached (30/30) - needs a human decision."
    variants = {}
    for key, reason in (("beh", R_BEH), ("adv", R_ADV), ("cap", R_CAP)):
        d = clone(base)
        mutate(d, lambda st, reason=reason: (finish(st, 2), blocked(reason)(st)))
        variants[key] = (d, reason)
    d, reason = variants["beh"]
    r = status_text(d)
    o = opts_in(r.stdout)
    check("[CP3-T09] status quotes the reason in plain words", r.returncode == 0 and quoted(r.stdout, P_BEH), r.stdout)
    check("[CP3-T09] status offers 2 or 3 options lettered A), B)[, C)]",
          2 <= len(o) <= 3 and [x[0] for x in o] == ["A", "B", "C"][:len(o)], r.stdout)
    n = rx(d, "next")
    try:
        nj = json.loads(n.stdout)
    except ValueError:
        nj = {}
    nopts = nj.get("options") if isinstance(nj, dict) else None
    check("[CP3-T09] next also carries 2 or 3 lettered options",
          isinstance(nopts, list) and 2 <= len(nopts) <= 3 and [x.get("letter") for x in nopts] == ["A", "B", "C"][:len(nopts)],
          n.stdout)
    check("[CP3-T09] next keeps the raw reason and adds a plain one", R_BEH in json.dumps(nj.get("action", "")) and nj.get("reason_plain") == P_BEH, nj)

    print("[CP3-T10] options fit the kind of block")
    texts = {}
    for key, (d, reason) in variants.items():
        rr = rx(d, "next")
        try:
            jj = json.loads(rr.stdout)
        except ValueError:
            jj = {}
        texts[key] = " ".join(x.get("text", "") for x in (jj.get("options") or []) if isinstance(x, dict)).lower()
        check(f"[CP3-T10] {key}: status quotes its own reason in plain words",
              quoted(status_text(d).stdout, {"beh": P_BEH, "adv": P_ADV, "cap": P_CAP}[key]), status_text(d).stdout)
    check("[CP3-T10] option sets differ by cause", len(set(texts.values())) == 3 and "" not in texts.values(), texts)
    check("[CP3-T10] round cap offers raising the limit or stopping",
          re.search(r"raise|increase|allow more|higher", texts["cap"]) and "stop" in texts["cap"], texts["cap"])
    check("[CP3-T10] test failure offers a retry or revising the step",
          re.search(r"again|retry|different approach", texts["beh"]) and re.search(r"change|revise|rewrite|adjust", texts["beh"]),
          texts["beh"])

    print("[CP3-T11] block output stays machine-readable")
    d, reason = variants["beh"]
    n = rx(d, "next")
    try:
        nj = json.loads(n.stdout)
    except ValueError:
        nj = None
    check("[CP3-T11] next is JSON with phase=blocked, stop_ok=true, action",
          isinstance(nj, dict) and nj.get("phase") == "blocked" and nj.get("stop_ok") is True and isinstance(nj.get("action"), str),
          n.stdout[:200])
    nj = nj or {}
    check("[CP3-T11] options are a list of {letter, text}",
          isinstance(nj.get("options"), list) and all(isinstance(x, dict) and set(("letter", "text")) <= set(x)
                                                      and re.fullmatch(r"[A-C]", str(x["letter"])) and str(x["text"]).strip()
                                                      for x in nj["options"]) and len(nj["options"]) >= 2, nj.get("options"))
    check("[CP3-T11] hooks object still present", isinstance(nj.get("hooks"), dict), nj.keys())

    print("[CP3-T12] odd reasons still render")
    d = clone(base)
    mutate(d, lambda st: (finish(st, 2), blocked("")(st)))
    r = status_text(d)
    check("[CP3-T12] empty reason: no traceback, generic plain line",
          r.returncode == 0 and TRACE not in r.stderr and re.search(r"block|stuck|stopped|need", r.stdout, re.I) is not None, r.stderr + r.stdout)
    odd = 'He said "hi"\nsecond line — café ✓'
    d = clone(base)
    mutate(d, lambda st: (finish(st, 2), blocked(odd)(st)))
    r = status_text(d)
    check("[CP3-T12] odd reason: no traceback and text kept intact",
          r.returncode == 0 and TRACE not in r.stderr and 'He said "hi"' in r.stdout and "café" in r.stdout and "second line" in r.stdout,
          r.stderr + r.stdout)
    check("[CP3-T12] odd reason still gets 2-3 options", 2 <= len(opts_in(r.stdout)) <= 3, r.stdout)
    pd = clone(base)
    mutate(pd, lambda st: st.update(paused_from="building", phase="paused", blocked_reason="paused"))
    r = status_text(pd)
    check("[CP3-T12] paused: 'paused' wording and no failure options",
          r.returncode == 0 and "paused" in r.stdout.lower() and not opts_in(r.stdout), r.stdout)

    # ------------------------------------------------------------ T14
    print("[CP3-T14] a stale gate is described plainly")
    stale = clone(base)

    def go_stale(st):
        finish(st, 2)
        st["checkpoints"]["CP3"]["gates"]["behavior"]["status"] = "stale"
        st["checkpoints"]["CP3"]["step"] = "behavior"
    mutate(stale, go_stale)
    r = status_text(stale)
    check("[CP3-T14] says the check must be re-run because the code changed",
          re.search(r"re-?run|run (it|them|the checks?) again|check again", r.stdout, re.I) and re.search(r"code .*changed|changed", r.stdout, re.I),
          r.stdout)
    check("[CP3-T14] no 'stale' and no fingerprint", not re.search(r"\bstale\b|fingerprint", r.stdout, re.I), r.stdout)

    # ------------------------------------------------------------ T15 / T16
    print("[CP3-T15] warnings and doctor talk to the agent")
    nowaive = clone(base)
    mutate(nowaive, lambda st: finish(st, 2))
    n = rx(nowaive, "next")
    try:
        warn = json.loads(n.stdout).get("warning") or ""
    except ValueError:
        warn = ""
    dr = rx(nowaive, "doctor")
    for label, txt in (("warning", warn), ("doctor", dr.stdout)):
        check(f"[CP3-T15] {label}: addressed to the agent (offer to the user, ask first)",
              re.search(r"offer", txt, re.I) and re.search(r"\bask\b", txt, re.I), txt)
        check(f"[CP3-T15] {label}: never tells the user to type a ratchet.py command", "ratchet.py" not in txt, txt)

    print("[CP3-T16] hooks skipped shows in status text")
    r = status_text(waived)
    lines = [l for l in r.stdout.splitlines() if re.search(r"hooks", l, re.I) and re.search(r"skipped", l, re.I)]
    check("[CP3-T16] waived: a plain line says hooks were skipped at the user's request",
          len(lines) == 1 and re.search(r"request|choice|chose|asked", lines[0], re.I) is not None, r.stdout)
    r = status_text(nowaive)
    check("[CP3-T16] hooks missing but not waived: no 'skipped' line", "skipped" not in r.stdout.lower(), r.stdout)
    inst = rx(waived, "install-hooks", "--host", "claude", "--scope", "project")
    r = status_text(waived)
    check("[CP3-T16] installing hooks removes the line", inst.returncode == 0 and "skipped" not in r.stdout.lower(),
          inst.stderr + r.stdout)

    # ------------------------------------------------------------ T13
    print("[CP3-T13] nothing the user sees says fingerprint, lens or slot")
    seen_text = {}
    for name, d in [("building", building), ("stale", stale), ("pre", pre), ("waived", waived), ("nowaive", nowaive),
                    ("beh", variants["beh"][0]), ("adv", variants["adv"][0]), ("cap", variants["cap"][0]),
                    ("done", alldone)]:
        seen_text[f"{name} status"] = status_text(d).stdout
        seen_text[f"{name} view"] = rx(d, "view").stdout
    for name, d in [("pre", pre), ("nowaive", nowaive)]:
        seen_text[f"{name} plan-summary"] = rx(d, "plan-summary").stdout
    for k in ("beh", "adv", "cap"):
        try:
            nj = json.loads(rx(variants[k][0], "next").stdout)
            seen_text[f"{k} next action"] = str(nj.get("reason_plain", "")) + " " + " ".join(
                str(x.get("text", "")) for x in nj.get("options", []) if isinstance(x, dict))
        except ValueError:
            seen_text[f"{k} next action"] = ""
    seen_text["hooks warning"] = warn
    seen_text["doctor"] = dr.stdout
    bad = {k: (JARGON.findall(v) or RAW_PHASE.findall(v)) for k, v in seen_text.items() if JARGON.search(v) or RAW_PHASE.search(v)}
    check("[CP3-T13] no jargon word or raw phase id in any user-facing output", not bad, bad)
    check("[CP3-T13] the outputs were actually produced", all(v.strip() for k, v in seen_text.items() if "warning" not in k),
          [k for k, v in seen_text.items() if not v.strip()])

    # ------------------------------------------------------------ T21
    print("[CP3-T22] options never offer something that re-blocks at once")
    REAL = {
        "per-cp cap (adv-record)": "CP3: adversarial review still has 2 finding(s) after 6 rounds \u2014 needs a human/director decision.",
        "per-cp cap (reviewers)": "CP3: 6 adversarial rounds used without approval \u2014 needs a human/director decision.",
        "run cap": "Run-wide adversarial round cap reached (30/30). Raising it is the user's call (config change + approve-changes).",
    }
    for label, reason in REAL.items():
        d = clone(base)
        mutate(d, lambda st, reason=reason: (finish(st, 2), blocked(reason)(st)))
        out = status_text(d).stdout
        low = out.lower()
        opts = opts_in(out)
        texts_low = [t.lower() for _, t in opts]
        check(f"[CP3-T22] {label}: no 'another round' option, offers raising the limit",
              "another round" not in low and re.search(r"raise the review limit", low) is not None, out)
        expected = 3 if label == "run cap" else 4
        check(f"[CP3-T22] {label}: {expected} options (CP1 order), simplify first and raise last",
              len(opts) == expected and bool(texts_low) and "simplify" in texts_low[0]
              and "raise" in texts_low[-1], out)

    print("[CP3-T23] block reasons are shown in plain words; raw text stays in status --json")
    REAL["behavior"] = "CP3: behavior gate failed 3 times \u2014 needs a human decision."
    REAL["ui"] = "CP3: UI gate failed 3 rounds \u2014 needs a human decision."
    REAL["stop valve"] = "The agent tried to stop 9 times without any gate progress \u2014 loop halted for the user to look at."
    for label, reason in REAL.items():
        d = clone(base)
        mutate(d, lambda st, reason=reason: (finish(st, 2), blocked(reason)(st)))
        out = status_text(d).stdout
        check(f"[CP3-T23] {label}: status text has no jargon", not JARGON.search(out), out)
        view = rx(d, "view").stdout
        check(f"[CP3-T23] {label}: view output has no jargon", not JARGON.search(view), view)
        js = rx(d, "status", "--json")
        try:
            sj = json.loads(js.stdout)
        except ValueError:
            sj = {}
        check(f"[CP3-T23] {label}: --json keeps the raw reason and adds a plain one",
              sj.get("blocked_reason") == reason and sj.get("blocked_reason_plain") and not JARGON.search(sj["blocked_reason_plain"]), sj)

    print("[CP3-T21] existing engine suites stay green")
    for t in ("test_gates.py", "test_hooks.py"):
        r = subprocess.run([sys.executable, str(ROOT / "tests" / t)], capture_output=True, text=True, timeout=590)
        check(f"[CP3-T21] {t} exits 0", r.returncode == 0, r.stdout[-300:] + r.stderr[-200:])

    # ------------------------------------------------------------ T24 (adv-round-06 fix: the per-checkpoint
    # round-limit branch must still offer a "change the step" option, alongside raise/stop, not just those two)
    print("[CP3-T24] per-checkpoint round limit still offers raise, change-the-step, and stop")
    d, reason = variants["adv"]
    out = status_text(d).stdout
    opts = opts_in(out)
    texts_low = [t.lower() for _, t in opts]
    check("[CP3-T24] per-checkpoint limit offers exactly 4 options (CP1 order: simplify, change, stop, raise)",
          len(opts) == 4, out)
    check("[CP3-T24] option 1 simplifies the step", len(texts_low) > 0 and "simplify" in texts_low[0], out)
    check("[CP3-T24] option 2 changes the step so the review notes no longer apply",
          len(texts_low) > 1 and "change the step" in texts_low[1], out)
    check("[CP3-T24] option 3 stops and keeps what is finished", len(texts_low) > 2 and "stop" in texts_low[2], out)
    check("[CP3-T24] option 4 (last) raises the review limit",
          len(texts_low) > 3 and "raise the review limit" in texts_low[3], out)
    cap_d, cap_reason = variants["cap"]
    cap_opts = opts_in(status_text(cap_d).stdout)
    cap_texts_low = [t.lower() for _, t in cap_opts]
    check("[CP3-T24] the run-wide cap branch stays distinct (3 options: simplify, stop, raise; no change-the-step)",
          len(cap_opts) == 3 and "change the step" not in " ".join(cap_texts_low), cap_opts)
    check("[CP3-T24] the run-wide cap branch still leads with simplify and ends with raise",
          bool(cap_texts_low) and "simplify" in cap_texts_low[0] and "raise" in cap_texts_low[-1], cap_opts)

    # ------------------------------------------------------------ T25 (adv-round-06 fix): _blocked_accept_note
    # must never claim "the last fix changed the code, these findings need a fresh look" when the blocked round's
    # own record has nothing open to begin with -- only the stale-file shortcut used to say that regardless.
    print("[CP3-T25] no stale 'fresh look'/accept line when the recorded round has nothing open")
    d25 = clone(base)
    old_fp = json.loads(rx(d25, "fingerprint").stdout)["id"]
    (Path(d25) / "src/a.js").write_text("export const a = 2; // changed after CP3's round was recorded\n")

    def _inject_approved_round_no_findings(st):
        finish(st, 2)
        st["checkpoints"]["CP3"]["adv_round"] = {
            "round": 1, "recorded": "2026-01-01T00:00:00+00:00", "opened_at": "2026-01-01T00:00:00+00:00",
            "fingerprint": old_fp, "nonce": "n1", "reviewers": ["host-both"]}
        blocked("Run-wide adversarial round cap reached (30/30) - needs a human decision.")(st)

    mutate(d25, _inject_approved_round_no_findings)
    nj = json.loads(rx(d25, "next").stdout)
    check("[CP3-T25] next prints no accept_note: the recorded round has no open findings",
          nj.get("accept_note") is None, nj)
    out25 = rx(d25, "status").stdout
    check("[CP3-T25] status prints no 'fresh look' or accept-finding line either",
          "fresh look" not in out25.lower() and "accept-finding" not in out25.lower(), out25)

    for d in KEEP:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
