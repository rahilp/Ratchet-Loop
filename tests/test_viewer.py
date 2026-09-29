#!/usr/bin/env python3
"""
Viewer delivery tests: background server, link reporting, access token, what is (not) served,
multiple lanes, and the director dashboard.   python3 tests/test_viewer.py
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
TEMPLATE = ENGINE.parent.parent / "assets" / "viewer.html"
PHASES = ["planning", "awaiting_plan_approval", "building", "awaiting_checkpoint_review", "awaiting_final_review",
          "planning_feedback", "blocked", "paused", "done", "cancelled"]
JARGON = re.compile(r"\b(fingerprint|lens|slot)\b", re.I)
PASS = FAIL = 0


def sh(*args, cwd):
    return subprocess.run([sys.executable, str(ENGINE), *args], cwd=cwd, capture_output=True, text=True)


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"  {detail}"))


def lane(base: Path, slug: str) -> Path:
    d = base / slug
    d.mkdir(parents=True)
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(c, cwd=d, check=True)
    (d / "a.js").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    sh("init", slug, "--slug", slug, cwd=d)
    run = d / ".ratchet" / "runs" / slug
    (run / "app-scout.json").write_text("{}")
    (run / "reference").mkdir()
    (run / "reference" / "prototype.html").write_text("<p>PROTO</p>")
    (run / "checkpoints.json").write_text(json.dumps({"feature": slug, "reference": {
        "type": "prototype", "paths": [f".ratchet/runs/{slug}/reference/prototype.html"]}, "checkpoints": [{
        "id": "CP1", "title": "A", "summary": "s", "done_when": ["a"],
        "gates": {"behavior": True, "ui": False, "adversarial": True},
        "tests": {"files": ["t.js"], "command": "true"}}]}))
    (run / "tests" / "CP1.json").write_text('{"cases": []}')
    return d


def say(d, text, transcripts):
    """The user types a message: the agent CLI writes its transcript and calls the prompt hook."""
    tp = Path(transcripts) / f"{d.name}.jsonl"
    with tp.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text},
                            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    return subprocess.run([sys.executable, str(ENGINE), "hook", "prompt", "--host", "claude"], cwd=d, text=True,
                          capture_output=True, input=json.dumps({"cwd": str(d), "prompt": text, "session_id": "s-owner",
                                                                 "transcript_path": str(tp)}))


def building_lane(base: Path, slug: str, transcripts: Path, waive: bool) -> Path:
    d = lane(base, slug)
    sh("config", "--set", "min_review_seconds=0", cwd=d)
    assert sh("plan-submit", cwd=d).returncode == 0
    say(d, "yes, build it", transcripts)
    r = sh("approve-plan", cwd=d)
    assert r.returncode == 0, r.stderr + r.stdout
    if waive:
        sh("ask", "hooks-waive", cwd=d)
        say(d, "yes, go ahead without hooks", transcripts)
        r = sh("hooks-waive", cwd=d)
        assert r.returncode == 0, r.stderr + r.stdout
    sh("view", cwd=d)  # builds viewer.html + viewer-data.json
    return d


def plain_viewer_checks(base: Path):
    src = TEMPLATE.read_text(encoding="utf-8")
    tr = base / "transcripts"
    tr.mkdir()
    env_home = tempfile.mkdtemp(prefix="ratchet-viewer-home-")
    os.environ["RATCHET_HOME"] = env_home
    os.environ["RATCHET_HOST_PROVIDER"] = "anthropic"
    plain = building_lane(base, "plain", tr, waive=False)
    waived = building_lane(base, "waivedlane", tr, waive=True)

    print("[CP3-T17] viewer data carries plain step labels")
    run = plain / ".ratchet/runs/plain"
    try:
        data = json.loads((run / "viewer-data.json").read_text())
    except ValueError:
        data = {}
    nxt = data.get("next") or {}
    check("[CP3-T17] viewer-data.json is valid and the run is building",
          data.get("state", {}).get("phase") == "building", data.get("state", {}).get("phase"))
    label = nxt.get("label")
    check("[CP3-T17] next carries a plain 'label' for the current step (not the agent instruction)",
          isinstance(label, str) and label.strip() != "" and label != nxt.get("action"), nxt)
    check("[CP3-T17] the label has no jargon, raw phase id, command name or step code",
          isinstance(label, str) and not JARGON.search(label) and not re.search(r"_|ratchet\.py|\bCP\d", label or "_"), label)
    check("[CP3-T17] hooks object present in the data", isinstance(data.get("hooks"), dict), data.get("hooks"))
    wdata = json.loads((waived / ".ratchet/runs/waivedlane/viewer-data.json").read_text())
    check("[CP3-T17] waived project: hooks.waived is true in the data", (wdata.get("hooks") or {}).get("waived") is True,
          wdata.get("hooks"))
    check("[CP3-T17] not-waived project: hooks.waived is not true", (data.get("hooks") or {}).get("waived") is not True,
          data.get("hooks"))

    print("[CP3-T18] viewer page maps phases and gates to plain labels")
    m = re.search(r"GATE_LABEL\s*=\s*\{[^}]*\}", src)
    gl = m.group(0) if m else ""
    check("[CP3-T18] gate labels are plain words",
          all(w in gl for w in ("Tests", "Look and feel", "Second opinion", "Your OK")), gl)
    check("[CP3-T18] no numbered jargon labels like '1 Behavior' / '3 Review'",
          not re.search(r"[\"']\d\s+(Behavior|UI|Review|You)\b", src), gl)
    missing = [p for p in PHASES if not re.search(rf"\b{p}\s*:\s*\[", src)]
    check("[CP3-T18] every engine phase has a plain label entry", not missing, missing)
    body = re.sub(r"<style.*?</style>", "", src, flags=re.S)
    literals = re.findall(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`', body)
    text_nodes = re.findall(r">([^<>]+)<", body)
    bad = [x for x in literals + text_nodes if JARGON.search(re.sub(r"\$\{[^}]*\}", "", x))]
    check("[CP3-T18] no user-visible string says fingerprint, lens or slot", not bad, bad[:3])

    print("[CP3-T19] hooks-skipped banner only when waived")
    whtml = (waived / ".ratchet/runs/waivedlane/viewer.html").read_text()
    phtml = (plain / ".ratchet/runs/plain/viewer.html").read_text()
    check("[CP3-T19] waived page embeds hooks.waived=true", '"waived": true' in whtml)
    check("[CP3-T19] not-waived page does not embed waived=true", '"waived": true' not in phtml)
    bm = re.search(r"hooks[^\n]{0,60}skipped|skipped[^\n]{0,60}hooks", src, re.I)
    check("[CP3-T19] template contains banner text saying hooks were skipped", bm is not None)
    if bm:
        before = src[max(0, bm.start() - 400):bm.start()]
        check("[CP3-T19] banner is conditional on the waived flag (not always shown)", "waived" in before, before[-200:])
    check("[CP3-T19] hooks access is guarded, so a missing hooks object cannot crash the page",
          re.search(r"hooks\s*\?\.\s*waived|hooks\s*&&[^;\n]{0,40}waived|\(\s*[\w.]*hooks\s*\|\|\s*\{\s*\}\s*\)\.waived", src) is not None)

    print("[CP3-T20] viewer stays self-contained")
    check("[CP3-T20] no src/href/import/url() to an external host",
          not re.search(r"""(src|href)\s*=\s*["']?https?://|@import|url\(\s*["']?https?://""", src, re.I))
    for d in (plain, waived):
        sh("view", "--stop", cwd=d)


def main():
    base = Path(tempfile.mkdtemp(prefix="ratchet-viewer-"))
    a, b = lane(base, "alpha"), lane(base, "beta")
    try:
        print("Link before any server")
        r = sh("plan-submit", cwd=a)
        check("plan-submit prints a VIEWER: file:// link", "VIEWER: file://" in r.stdout, r.stdout)

        print("Background live server")
        r = sh("view", "--serve", cwd=a)
        info = json.loads((a / ".ratchet/viewer.json").read_text())
        url = info["url"]
        check("starts in the background and returns immediately", r.returncode == 0 and url.startswith("http://127.0.0.1:"))
        check("page served", get(url)[0] == 200)
        code, body = get(url + "viewer-data.json")
        check("live data served", code == 200 and json.loads(body)["state"]["slug"] == "alpha")
        check("prototype served", get(url + "reference/prototype.html")[1] == b"<p>PROTO</p>")
        check("state.json NOT served", get(url + "state.json")[0] == 404)
        check("evidence NOT served", get(url + "evidence/CP1/red.log")[0] == 404)
        check("second --serve reuses it", "already running" in sh("view", "--serve", cwd=a).stdout)
        check("plan-submit now prints the live URL", f"VIEWER: {url}" in sh("plan-submit", cwd=a).stdout)
        st = json.loads(sh("status", "--json", cwd=a).stdout)
        check("status --json reports viewer_url + live", st["viewer_url"] == url and st["viewer_live"])

        print("Second lane, remote bind with token")
        sh("view", "--serve", "--bind", "0.0.0.0", "--public-host", "box.test", cwd=b)
        ib = json.loads((b / ".ratchet/viewer.json").read_text())
        check("different port from lane alpha", ib["port"] != info["port"])
        check("URL uses the public host and carries a token", ib["url"].startswith("http://box.test:") and "?t=" in ib["url"])
        local = f"http://127.0.0.1:{ib['port']}/"
        check("no token → 403", get(local)[0] == 403)
        check("wrong token → 403", get(local + "?t=nope")[0] == 403)
        check("right token → 200", get(local + "?t=" + ib["token"])[0] == 200)
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        check("token not visible in the process list", ib["token"] not in ps)

        print("Director dashboard")
        sh("lanes", "--serve", "--scan", str(base), cwd=base)
        dash = json.loads((base / ".ratchet-lanes.json").read_text())["url"]
        code, body = get(dash + "lanes.json")
        lanes = json.loads(body)
        check("lists both lanes with their viewer links", sorted(l["slug"] for l in lanes) == ["alpha", "beta"]
              and all("viewer_url" in l for l in lanes))
        check("proxies a lane's viewer", get(dash + "lane/0/")[0] == 200)
        check("proxies a lane's live data", json.loads(get(dash + "lane/1/viewer-data.json")[1])["state"]["slug"] in ("alpha", "beta"))
        check("proxies a lane's prototype", get(dash + "lane/0/reference/prototype.html")[1] == b"<p>PROTO</p>")
        plain_viewer_checks(base)
    finally:
        sh("lanes", "--stop", "--scan", str(base), cwd=base)
        for d in (a, b):
            sh("view", "--stop", cwd=d)
    left = [l for l in subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.splitlines()
            if "ratchet.py" in l and "--foreground" in l and str(base) in l]
    check("--stop leaves no server running", not left, str(left))
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
