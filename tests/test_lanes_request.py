#!/usr/bin/env python3
"""
CP8 tests: `lanes --request <name> [--json]` tracks a request's lanes through to merged.   python3 tests/test_lanes_request.py

A request is made with the real `lanes-start` (CP7); lane states are set the cheap way (the lane agent's own `init`,
then a hand-written state.json for a finished, waiting or blocked run); a merged lane is made by the real `integrate`
(CP2) or by plain git. The throwaway projects come from tests/test_lanes_start.py (imported, not edited).

Contract the tests pin (the build must match):
  - `lanes --request NAME --json` prints exactly {"request": NAME, "lanes": [...]}; each lane has lane, branch,
    worktree, worktree_missing (bool), phase ("not_started" before the lane's init), done, total, waiting_on
    ("director" | "human" | null), awaiting_since (the stored time string, or null), merged (bool) and
    next_director_action ("integrate" | null), in the order of the request record
  - merged = the lane branch's tip is an ancestor of main (git decides; integrations.jsonl never changes it)
  - next_director_action is "integrate" only for a lane in phase done that is not merged and whose worktree exists
  - the text form has one block per lane in the same order; a block starts at the line that names the lane and holds
    the phase, "done/total", the waiting party and the awaiting_since string as stored, "merged" or "not merged", the
    word integrate (as a word, only for a lane that needs it), "not started" for a lane without a run, and "gone" for a
    lane whose worktree was removed; absolute paths are cut out before the checks
  - it is read from the main checkout's .ratchet/requests/NAME.json even from inside a lane worktree, needs no
    director, and changes nothing

Writes a per-case JSON report to RATCHET_TEST_REPORT when that env var is set: {"cases": [{"id": "CP8-T01",
"status": "passed"|"failed", "message": "..."}]}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trust_helpers as th  # noqa: E402

ENGINE = th.ENGINE
PASS = FAIL = 0
CASE_STATUS: dict[str, bool] = {}
CASE_MESSAGES: dict[str, list] = {}
ID_RE = re.compile(r"\[(CP8-[TX]\d+[a-z]?)\]")
REQ = "checkout-flow"
SINCE_MID = "2026-10-01T10:00:00.000000+00:00"
SINCE_BLOCKED = "2026-10-02T11:30:00.000000+00:00"
SINCE_ASK = "2026-10-03T12:45:00.000000+00:00"
LANES3 = [("zeta", "done"), ("alpha", "merged"), ("mid", "plan")]


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and bool(cond)
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:500]}"))


class Stop(Exception):
    pass


def need(name, cond, detail=""):
    check(name, cond, detail)
    if not cond:
        raise Stop(name)


def tl():
    """The CP7 fixtures, imported inside each case so a problem there is one failed case, not a crash at import."""
    import test_lanes_start as m
    return m


# ------------------------------------------------------------------------------------------------ fixtures

_REBASELINE = ("import importlib.util, sys\nfrom pathlib import Path\n"
               "s = importlib.util.spec_from_file_location('ratchet_fixture', sys.argv[1])\n"
               "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
               "for a in sys.argv[2:]:\n    m.record_chain(Path(a))\n")
ENVS = {}   # worktree -> the env its Ratchet commands run with


def rebaseline(lane, *files):
    """A hand edit of a run file is re-baselined with the engine's own record_chain (what accept-state does), so
    the fixture does not look tampered with."""
    r = subprocess.run([sys.executable, "-c", _REBASELINE, str(ENGINE), *map(str, files)], cwd=lane,
                       env=ENVS[str(lane)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout


def set_state(lane, slug, **kw):
    p = Path(lane) / ".ratchet/runs" / slug / "state.json"
    st = json.loads(p.read_text())
    st.update(kw)
    p.write_text(json.dumps(st, indent=2) + "\n")
    rebaseline(lane, p)


def write_plan(lane, slug, n):
    run = Path(lane) / ".ratchet/runs" / slug
    cps = [{"id": f"CP{i}", "title": f"Step {i}", "summary": "s", "done_when": ["works"],
            "gates": {"behavior": True, "ui": False, "adversarial": True}, "risk": "low",
            "tests": {"files": [f"tests/cp{i}.test.js"], "command": "true"}} for i in range(1, n + 1)]
    (run / "checkpoints.json").write_text(json.dumps({"feature": "x", "objective": {}, "checkpoints": cps}))
    rebaseline(lane, run / "checkpoints.json")


def commit_in(lane, name):
    m = tl()
    d = Path(lane)
    (d / "src").mkdir(exist_ok=True)
    (d / "src" / f"{name}.js").write_text(f"export const {name.replace('-', '_')} = 1;\n")
    th.git(d, "add", f"src/{name}.js")
    th.git(d, "commit", "-qm", f"{name}: work")


def setup_lane(w, name, kind):
    """kind: done | merged | plan | fresh | blocked | question | ghost. Every lane gets one commit of its own."""
    lane = Path(w.main.parent / f"{w.main.name}-{name}")
    commit_in(lane, name)
    if kind == "fresh":
        return lane
    slug = f"{name}-run"
    ENVS[str(lane)] = w.env
    r = th.rx(lane, w.env, "init", f"{name} work", "--slug", slug)
    assert r.returncode == 0, r.stdout + r.stderr
    write_plan(lane, slug, 2)
    done2 = {"CP1": {"status": "done"}, "CP2": {"status": "done"}}
    if kind in ("done", "merged", "ghost"):
        set_state(lane, slug, phase="done", checkpoints=done2, current=None, awaiting_since=None)
    elif kind == "plan":
        set_state(lane, slug, phase="awaiting_plan_approval", awaiting_since=SINCE_MID)
    elif kind == "blocked":
        set_state(lane, slug, phase="blocked", awaiting_since=SINCE_BLOCKED,
                  blocked_reason="CP1: adversarial review still has 1 finding(s) after 3 rounds",
                  checkpoints={"CP1": {"status": "in_progress"}}, current="CP1")
    elif kind == "question":
        set_state(lane, slug, phase="awaiting_checkpoint_review", awaiting_since=SINCE_ASK,
                  checkpoints={"CP1": {"status": "in_progress"}}, current="CP1",
                  pending={"action": "choice", "mode": "A or B", "asked_at": SINCE_ASK,
                           "phase": "awaiting_checkpoint_review"})
    return lane


def integrate(w, lane):
    return subprocess.run([sys.executable, str(ENGINE), "--root", str(w.main), "integrate", lane], cwd=w.main,
                          capture_output=True, text=True, env=w.denv, timeout=150)


def make_world(request=REQ, lanes=LANES3):
    m = tl()
    w = m.build()
    r = w.start(request="Checkout flow work", lanes=[f"{n}=Do the {n} part" for n, _ in lanes], name=request, js=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for n, kind in lanes:
        setup_lane(w, n, kind)
    for n, kind in lanes:
        if kind == "merged":
            r = integrate(w, n)
            assert r.returncode == 0, r.stdout + r.stderr
    for n, kind in lanes:
        if kind == "ghost":
            th.git(w.main, "worktree", "remove", "--force", str(w.main.parent / f"{w.main.name}-{n}"))
    return w


_SHARED = {}


def shared():
    if "w" not in _SHARED:
        _SHARED["w"] = make_world()
    return _SHARED["w"]


def lane_dir(w, name):
    return w.main.parent / f"{w.main.name}-{name}"


def lanes_req(w, request=REQ, cwd=None, env=None, js=True, extra=()):
    args = ["lanes", "--request", request] + (["--json"] if js else []) + list(extra)
    return th.rx(cwd or w.main, env or w.denv, *args)


def doc_of(r, cid, what):
    m = tl()
    need(f"[{cid}] {what}: exit 0 with the engine's own output", m.known(r) and r.returncode == 0,
         (r.returncode, m.said(r)[:300]))
    try:
        return json.loads(r.stdout)
    except Exception as e:
        need(f"[{cid}] {what}: --json prints one JSON document", False, f"{e}: {r.stdout[:200]}")


def entry(doc, name):
    xs = [x for x in doc.get("lanes", []) if x.get("lane") == name]
    return xs[0] if xs else {}


def blocks(text, names):
    """{name: lower-case block} cut at the first whole-word mention of each lane name; None when one is missing or
    the names are not in the given order."""
    pos = []
    for n in names:
        m = re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text)
        if not m:
            return None
        pos.append(m.start())
    if pos != sorted(pos):
        return None
    out = {}
    for i, n in enumerate(names):
        out[n] = text[pos[i]:(pos[i + 1] if i + 1 < len(pos) else len(text))].lower()
    return out


def text_blocks(w, cid, names, request=REQ, what="text form", cwd=None):
    m = tl()
    r = lanes_req(w, request, js=False, cwd=cwd)
    need(f"[{cid}] {what}: exit 0 with the engine's own output", m.known(r) and r.returncode == 0,
         (r.returncode, m.said(r)[:300]))
    t = m.said(r)
    b = blocks(t, names)
    need(f"[{cid}] {what}: every lane named, in request order", b is not None, t[:400])
    return b


def append_record(w, lines):
    p = w.main / ".ratchet/integrations.jsonl"
    with p.open("a") as f:
        for ln in lines:
            f.write(ln if isinstance(ln, str) else json.dumps(ln) + "\n")


def rec(lane, result, detail="x"):
    return {"time": "2026-10-01T09:00:00.000000+00:00", "lane": lane, "run": f"{lane}-run", "commits": [],
            "suite": "failed" if result == "rolled-back" else "none", "result": result, "detail": detail}


# ------------------------------------------------------------------------------------------------ cases

def t01_every_lane_with_phase_steps_and_waiting():
    cid = "CP8-T01"
    w = shared()
    b = text_blocks(w, cid, ["zeta", "alpha", "mid"])
    check(f"[{cid}] zeta: phase done, 2/2", "done" in b["zeta"] and "2/2" in b["zeta"], b["zeta"])
    check(f"[{cid}] alpha: phase done, 2/2", "done" in b["alpha"] and "2/2" in b["alpha"], b["alpha"])
    check(f"[{cid}] mid: awaiting_plan_approval, 0/2", "awaiting_plan_approval" in b["mid"] and "0/2" in b["mid"], b["mid"])
    check(f"[{cid}] mid: waiting on the director since the recorded time",
          "director" in b["mid"] and SINCE_MID.lower() in b["mid"], b["mid"])
    d = doc_of(lanes_req(w), cid, "json form")
    check(f"[{cid}] json: phases, steps and waiting",
          (entry(d, "zeta").get("phase"), entry(d, "zeta").get("done"), entry(d, "zeta").get("total")) == ("done", 2, 2)
          and entry(d, "mid").get("phase") == "awaiting_plan_approval" and entry(d, "mid").get("done") == 0
          and entry(d, "mid").get("total") == 2 and entry(d, "mid").get("waiting_on") == "director"
          and entry(d, "mid").get("awaiting_since") == SINCE_MID, d)


def t02_request_order():
    cid = "CP8-T02"
    w = shared()
    d = doc_of(lanes_req(w), cid, "json form")
    check(f"[{cid}] json lanes are exactly [zeta, alpha, mid]", [x.get("lane") for x in d.get("lanes", [])] ==
          ["zeta", "alpha", "mid"], d)
    text_blocks(w, cid, ["zeta", "alpha", "mid"])


def t03_merged_by_integrate():
    cid = "CP8-T03"
    w = shared()
    d = doc_of(lanes_req(w), cid, "json form")
    check(f"[{cid}] alpha merged true, zeta merged false", entry(d, "alpha").get("merged") is True
          and entry(d, "zeta").get("merged") is False, d)
    b = text_blocks(w, cid, ["zeta", "alpha", "mid"])
    check(f"[{cid}] text: alpha merged, zeta not merged", "merged" in b["alpha"] and "not merged" not in b["alpha"]
          and "not merged" in b["zeta"], b)


def t04_integrate_is_the_next_action():
    cid = "CP8-T04"
    w = shared()
    d = doc_of(lanes_req(w), cid, "json form")
    check(f"[{cid}] zeta next action integrate; alpha and mid null",
          entry(d, "zeta").get("next_director_action") == "integrate"
          and "next_director_action" in entry(d, "alpha") and entry(d, "alpha")["next_director_action"] is None
          and "next_director_action" in entry(d, "mid") and entry(d, "mid")["next_director_action"] is None, d)
    b = text_blocks(w, cid, ["zeta", "alpha", "mid"])
    ints = {k: bool(re.search(r"\bintegrate\b", v)) for k, v in b.items()}
    check(f"[{cid}] text names integrate for zeta only", ints == {"zeta": True, "alpha": False, "mid": False}, b)


def t05_worktree_gone():
    cid = "CP8-T05"
    w = make_world("ghost-req", [("zeta", "done"), ("ghost", "ghost"), ("mid", "plan")])
    d = doc_of(lanes_req(w, "ghost-req"), cid, "json form")
    check(f"[{cid}] order kept", [x.get("lane") for x in d.get("lanes", [])] == ["zeta", "ghost", "mid"], d)
    g = entry(d, "ghost")
    check(f"[{cid}] ghost: worktree_missing true, not merged, next action null",
          g.get("worktree_missing") is True and g.get("merged") is False and "next_director_action" in g
          and g["next_director_action"] is None, g)
    check(f"[{cid}] others fine", entry(d, "zeta").get("worktree_missing") is False and entry(d, "zeta").get("phase") == "done"
          and entry(d, "mid").get("phase") == "awaiting_plan_approval", d)
    b = text_blocks(w, cid, ["zeta", "ghost", "mid"], "ghost-req")
    check(f"[{cid}] text: worktree gone and what to do", "gone" in b["ghost"] and
          ("recreate" in b["ghost"] or "drop" in b["ghost"]) and "traceback" not in b["ghost"], b["ghost"])
    check(f"[{cid}] text: others still show phases", "done" in b["zeta"] and "awaiting_plan_approval" in b["mid"], b)


def t06_merged_then_cleaned_up():
    cid = "CP8-T06"
    w = make_world()
    th.git(w.main, "worktree", "remove", "--force", str(lane_dir(w, "alpha")))
    d = doc_of(lanes_req(w), cid, "json form")
    a = entry(d, "alpha")
    check(f"[{cid}] alpha merged true and worktree_missing true", a.get("merged") is True
          and a.get("worktree_missing") is True and a.get("next_director_action") is None, a)
    b = text_blocks(w, cid, ["zeta", "alpha", "mid"])
    t = b["alpha"]
    check(f"[{cid}] text: merged, not called a problem", "merged" in t and "not merged" not in t and "problem" not in t
          and "recreate" not in t and "drop" not in t, t)


def t07_lane_not_started():
    cid = "CP8-T07"
    w = make_world("fresh-req", [("zeta", "done"), ("fresh", "fresh")])
    d = doc_of(lanes_req(w, "fresh-req"), cid, "json form")
    f = entry(d, "fresh")
    check(f"[{cid}] fresh: not_started, 0/0, waiting null, not merged, no action",
          f.get("phase") == "not_started" and f.get("done") == 0 and f.get("total") == 0
          and "waiting_on" in f and f["waiting_on"] is None and f.get("merged") is False
          and "next_director_action" in f and f["next_director_action"] is None
          and f.get("worktree_missing") is False, f)
    check(f"[{cid}] order kept", [x.get("lane") for x in d.get("lanes", [])] == ["zeta", "fresh"], d)
    b = text_blocks(w, cid, ["zeta", "fresh"], "fresh-req")
    check(f"[{cid}] text says it has not started", re.search(r"not[ _]started", b["fresh"]) is not None, b["fresh"])


def t08_failed_attempts_are_not_merged():
    cid = "CP8-T08"
    w = make_world()
    append_record(w, [rec("zeta", "rolled-back", "suite failed"), rec("zeta", "refused", "main dirty"),
                      rec("alpha", "rolled-back", "suite failed")])
    d = doc_of(lanes_req(w), cid, "json form")
    check(f"[{cid}] zeta merged false, integrate still offered",
          entry(d, "zeta").get("merged") is False and entry(d, "zeta").get("next_director_action") == "integrate", d)
    check(f"[{cid}] alpha merged true", entry(d, "alpha").get("merged") is True, d)


def t09_missing_or_damaged_record():
    cid = "CP8-T09"
    w = make_world()
    rp = w.main / ".ratchet/integrations.jsonl"
    rp.unlink()
    d = doc_of(lanes_req(w), cid, "no record")
    check(f"[{cid}] no record: alpha merged, zeta not", entry(d, "alpha").get("merged") is True
          and entry(d, "zeta").get("merged") is False, d)
    rp.write_text("this is not json\n" + json.dumps(rec("alpha", "merged")) + "\n" + '{"time": "2026-10-01", "lane": "zet')
    r = lanes_req(w)
    check(f"[{cid}] damaged record: no traceback", "Traceback" not in (r.stdout + r.stderr), r.stderr[:300])
    d = doc_of(r, cid, "damaged record")
    check(f"[{cid}] damaged record: alpha merged, zeta not", entry(d, "alpha").get("merged") is True
          and entry(d, "zeta").get("merged") is False, d)
    rp.write_text(json.dumps(rec("zeta", "merged")) + "\n")   # a record that claims a merge git does not show
    d = doc_of(lanes_req(w), cid, "record claiming a merge")
    check(f"[{cid}] a record line never makes a lane merged", entry(d, "zeta").get("merged") is False, d)


def t10_only_the_named_request():
    cid = "CP8-T10"
    m = tl()
    w = shared()
    if not (w.main / ".ratchet/requests/search-fix.json").exists():
        r = w.start(request="Fix search", lanes=["late=Fix the search"], name="search-fix", js=True)
        need(f"[{cid}] second request started", r.returncode == 0, r.stdout + r.stderr)
        setup_lane(w, "late", "plan")
    d = doc_of(lanes_req(w, "search-fix"), cid, "search-fix")
    check(f"[{cid}] only late is listed", [x.get("lane") for x in d.get("lanes", [])] == ["late"]
          and d.get("request") == "search-fix", d)
    d = doc_of(lanes_req(w), cid, "checkout-flow")
    check(f"[{cid}] checkout-flow does not list late", [x.get("lane") for x in d.get("lanes", [])] ==
          ["zeta", "alpha", "mid"], d)
    r = lanes_req(w, "nope", js=False)
    t = m.said(r)
    lines = [x for x in t.splitlines() if x.strip()]
    check(f"[{cid}] unknown request: non-zero, no traceback", m.known(r) and r.returncode != 0 and "Traceback" not in t, t[:300])
    check(f"[{cid}] one plain line naming nope and the existing requests", len(lines) == 1 and "nope" in lines[0]
          and "checkout-flow" in lines[0] and "search-fix" in lines[0] and "no request" in lines[0].lower(), lines)


def t11_blocked_and_question_lanes():
    cid = "CP8-T11"
    w = make_world("stuck", [("stuck", "blocked"), ("asked", "question")])
    d = doc_of(lanes_req(w, "stuck"), cid, "json form")
    s, a = entry(d, "stuck"), entry(d, "asked")
    check(f"[{cid}] blocked lane: director, its own time, no integrate", s.get("waiting_on") == "director"
          and s.get("awaiting_since") == SINCE_BLOCKED and s.get("next_director_action") is None, s)
    check(f"[{cid}] question lane: director, its own time, no integrate", a.get("waiting_on") == "director"
          and a.get("awaiting_since") == SINCE_ASK and a.get("next_director_action") is None, a)
    b = text_blocks(w, cid, ["stuck", "asked"], "stuck")
    for n, since in (("stuck", SINCE_BLOCKED), ("asked", SINCE_ASK)):
        check(f"[{cid}] {n} text names the director and the time, never 'ask the user'",
              "director" in b[n] and since.lower() in b[n] and "ask the user" not in b[n], b[n])


def _snapshot(w):
    m = tl()
    snap = {"main_ratchet": m.tree_bytes(w.main / ".ratchet"), "heads": m.gout(w.main, "for-each-ref", "refs/heads"),
            "head": m.gout(w.main, "rev-parse", "HEAD"), "remote": m.gout(w.remote, "for-each-ref")}
    for n in ("zeta", "alpha", "mid"):
        snap["run_" + n] = m.tree_bytes(lane_dir(w, n) / ".ratchet")
    return snap


def t12_changes_nothing():
    cid = "CP8-T12"
    w = shared()
    first = lanes_req(w)
    need(f"[{cid}] the view works", first.returncode == 0 and tl().known(first), tl().said(first)[:300])
    before = _snapshot(w)
    for js in (False, True, False, True):
        lanes_req(w, js=js)
    after = _snapshot(w)
    bad = [k for k in before if before[k] != after[k]]
    check(f"[{cid}] no state, record, branch, commit or remote ref moved", not bad, bad)
    locks = [str(p) for base in (w.main / ".ratchet", *(lane_dir(w, n) / ".ratchet" for n in ("zeta", "alpha", "mid")))
             for p in base.rglob("*") if "lock" in p.name]
    check(f"[{cid}] no lock file left behind", not locks, locks)


def t13_json_and_text_agree():
    cid = "CP8-T13"
    w = shared()
    d = doc_of(lanes_req(w), cid, "json form")
    check(f"[{cid}] top-level keys are exactly request and lanes", sorted(d) == ["lanes", "request"] and d["request"] == REQ, sorted(d))
    keys = {"lane", "branch", "worktree", "worktree_missing", "phase", "done", "total", "waiting_on", "awaiting_since",
            "merged", "next_director_action"}
    check(f"[{cid}] every lane has exactly the pinned keys", all(set(x) == keys for x in d["lanes"]),
          [sorted(set(x) ^ keys) for x in d["lanes"]])
    b = text_blocks(w, cid, [x["lane"] for x in d["lanes"]])
    for x in d["lanes"]:
        t = b[x["lane"]]
        ok = x["phase"] in t and f"{x['done']}/{x['total']}" in t
        ok = ok and (x["waiting_on"] is None or x["waiting_on"] in t)
        ok = ok and (x["awaiting_since"] is None or x["awaiting_since"].lower() in t)
        ok = ok and (("merged" in t and "not merged" not in t) if x["merged"] else "not merged" in t)
        check(f"[{cid}] {x['lane']}: the text carries the json's facts", ok, (x, t))


def t14_plain_lanes_unchanged():
    cid = "CP8-T14"
    m = tl()
    w = shared()
    r = th.rx(w.main, w.denv, "lanes", "--scan", str(w.base), "--json")
    need(f"[{cid}] plain --json works", m.known(r) and r.returncode == 0, m.said(r)[:300])
    d = json.loads(r.stdout)
    check(f"[{cid}] a JSON list of run entries with the old keys", isinstance(d, list) and d and all(
        {"root", "slug", "phase", "done", "total", "waiting_on"} <= set(x) for x in d), d if not isinstance(d, list) else [sorted(x) for x in d][:1])
    extra = [k for x in d if isinstance(x, dict) for k in x if k in ("request", "merged", "next_director_action", "lane", "worktree_missing")]
    check(f"[{cid}] no request, merged or next_director_action fields added", not extra, extra)
    r = th.rx(w.main, w.denv, "lanes", "--scan", str(w.base))
    t = m.said(r)
    check(f"[{cid}] text: a line per run and a viewer line, nothing about requests or merging",
          r.returncode == 0 and t.count("viewer:") == len(d) and "request" not in t.lower() and "merged" not in t.lower()
          and not re.search(r"\bintegrate\b", t.lower()), t[:500])
    check(f"[{cid}] text: slug, phase and done/total on the run lines",
          all(x["slug"] in t and f"{x['done']}/{x['total']}" in t for x in d if "slug" in x), t[:500])
    r = th.rx(w.main, w.denv, "lanes", "--scan", str(w.base), "--stop")
    check(f"[{cid}] --stop still reports no dashboard running", r.returncode == 0 and "dashboard" in r.stdout.lower(), r.stdout + r.stderr)


def t15_inside_a_lane():
    cid = "CP8-T15"
    m = tl()
    w = shared()
    ref = doc_of(lanes_req(w), cid, "from main")
    for n in ("zeta", "mid"):
        r = th.rx(lane_dir(w, n), w.denv, "lanes", "--request", REQ, "--json")
        d = doc_of(r, cid, f"from the {n} worktree")
        check(f"[{cid}] same output from the {n} worktree as from main", d == ref, d)
    rf = w.main / ".ratchet/requests" / f"{REQ}.json"
    hidden = rf.with_name("hidden.bak")
    rf.rename(hidden)
    try:
        r = th.rx(lane_dir(w, "zeta"), w.denv, "lanes", "--request", REQ)
        t = m.raw_said(r)
        lines = [x for x in t.splitlines() if x.strip()]
        check(f"[{cid}] record missing: non-zero, one plain line naming the main checkout, no traceback",
              r.returncode != 0 and "Traceback" not in t and len(lines) == 1 and str(w.main) in lines[0], t[:400])
    finally:
        hidden.rename(rf)


def t16_merged_by_hand():
    cid = "CP8-T16"
    w = make_world(REQ, [("zeta", "done"), ("alpha", "done"), ("mid", "plan")])
    check(f"[{cid}] no integration record exists", not (w.main / ".ratchet/integrations.jsonl").exists(), "")
    r = tl().gitx(w.main, "merge", "--ff-only", "zeta")
    need(f"[{cid}] fast-forward merge of zeta by hand", r.returncode == 0, r.stdout + r.stderr)
    d = doc_of(lanes_req(w), cid, "after the fast-forward")
    check(f"[{cid}] zeta merged, no integrate offered; mid not merged", entry(d, "zeta").get("merged") is True
          and entry(d, "zeta").get("next_director_action") is None and entry(d, "mid").get("merged") is False, d)
    b = text_blocks(w, cid, ["zeta", "alpha", "mid"])
    check(f"[{cid}] text says merged for zeta", "merged" in b["zeta"] and "not merged" not in b["zeta"], b["zeta"])
    r = tl().gitx(w.main, "merge", "--no-ff", "-m", "merge alpha by hand", "alpha")
    need(f"[{cid}] ordinary merge of alpha by hand", r.returncode == 0, r.stdout + r.stderr)
    d = doc_of(lanes_req(w), cid, "after the ordinary merge")
    check(f"[{cid}] alpha merged", entry(d, "alpha").get("merged") is True and entry(d, "alpha").get("next_director_action") is None, d)
    commit_in(lane_dir(w, "zeta"), "zeta-more")
    d = doc_of(lanes_req(w), cid, "after new commits on zeta")
    check(f"[{cid}] new commits after the merge: not merged, integrate offered again",
          entry(d, "zeta").get("merged") is False and entry(d, "zeta").get("next_director_action") == "integrate", d)


def t17_any_session_can_run_it():
    cid = "CP8-T17"
    m = tl()
    w = shared()
    docs = []
    for label, env in (("director", w.denv), ("builder", dict(w.env, RATCHET_ROLE="builder"))):
        for where, cwd in (("main", w.main), ("lane", lane_dir(w, "zeta"))):
            r = th.rx(cwd, env, "lanes", "--request", REQ, "--json")
            check(f"[{cid}] {label} in {where}: exit 0, not refused", m.known(r) and r.returncode == 0,
                  (r.returncode, m.said(r)[:300]))
            try:
                docs.append(json.loads(r.stdout))
            except Exception:
                docs.append(None)
    check(f"[{cid}] all four runs show the same lanes and facts", docs[0] is not None and all(x == docs[0] for x in docs), docs)


TESTS = [
    ("CP8-T01", t01_every_lane_with_phase_steps_and_waiting),
    ("CP8-T02", t02_request_order),
    ("CP8-T03", t03_merged_by_integrate),
    ("CP8-T04", t04_integrate_is_the_next_action),
    ("CP8-T05", t05_worktree_gone),
    ("CP8-T06", t06_merged_then_cleaned_up),
    ("CP8-T07", t07_lane_not_started),
    ("CP8-T08", t08_failed_attempts_are_not_merged),
    ("CP8-T09", t09_missing_or_damaged_record),
    ("CP8-T10", t10_only_the_named_request),
    ("CP8-T11", t11_blocked_and_question_lanes),
    ("CP8-T12", t12_changes_nothing),
    ("CP8-T13", t13_json_and_text_agree),
    ("CP8-T14", t14_plain_lanes_unchanged),
    ("CP8-T15", t15_inside_a_lane),
    ("CP8-T16", t16_merged_by_hand),
    ("CP8-T17", t17_any_session_can_run_it),
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
    only = [a.upper() for a in sys.argv[1:]]
    for cid, fn in TESTS:
        if only and not any(cid.endswith(o) for o in only):
            continue
        CASE_STATUS.setdefault(cid, True)
        try:
            fn()
        except Stop:
            pass
        except Exception as e:
            FAIL += 1
            CASE_STATUS[cid] = False
            CASE_MESSAGES.setdefault(cid, []).append(f"[{cid}] stopped early: {type(e).__name__}: {e}"[:200])
            print(f"  ✗ [{cid}] stopped early: {type(e).__name__}: {e}")
            traceback.print_exc()
    write_report()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
