#!/usr/bin/env python3
"""
Viewer delivery tests: background server, link reporting, access token, what is (not) served,
multiple lanes, and the director dashboard.   python3 tests/test_viewer.py
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "skills" / "ratchet-orchestrator" / "scripts" / "ratchet.py"
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
